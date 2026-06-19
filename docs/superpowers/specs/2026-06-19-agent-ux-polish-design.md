# Agent UX Polish — Design Spec

**Date:** 2026-06-19
**Status:** Approved
**Scope:** Frontend + backend improvements to core agent interaction experience

## Motivation

The agent execution pipeline was recently unified (container refactor, 2026-06-18), but the
user-facing experience around it still has rough edges:

- WebSocket disconnection shows a raw "error" state with no recovery path
- Agent errors display unreadable stack traces ("ProcessTransport is not ready for writing")
- Tool execution lacks progress visibility — users can't tell what the agent is doing or how long it will take
- Consecutive file operations flood the chat with repetitive cards

Each of these is a drop-off point. Users who encounter an unrecoverable-looking error or an
uninformative waiting state are likely to abandon the session.

## Scope

Three independent modules, each deliverable in order:

| # | Module | Backend | Frontend |
|---|--------|---------|----------|
| 1 | WebSocket reconnect + session resume | WS message protocol | `useWebSocket` hook |
| 2 | Error severity classification + recovery actions | `event_pipeline.py` | `ErrorCard` component |
| 3 | Tool-call visualization enhancement | None (uses existing `duration_ms`) | `ToolCard` + `ToolGroupRenderer` |

---

## Module 1: WebSocket Reconnect + Session Resume

### Goal

Users who briefly lose connectivity (tab sleep, network flap, VPN switch) reconnect
transparently and continue where they left off without losing context.

### Connection States

```
connected ──► reconnecting ──► recovered     (disconnected < 30s, seq resume)
                           └──► history_sync (disconnected 30s-5min, pull history)
                           └──► expired      (disconnected > 5min, session gone)
```

### Frontend: `useWebSocket` Changes

**Reconnect logic:**
- On disconnect, enter `reconnecting` state with exponential backoff (1s → 2s → 4s, cap 8s, max 3 retries)
- Track `lastSeq` per session (received on every WS message, stored in memory + `sessionStorage`)
- On reconnect: send `{ type: "resume", session_id, last_seq }` instead of a new `chat` message
- If reconnect succeeds and server streams catch-up events, mark recovered
- If reconnect fails or is rejected, fall back to history pull

**State machine additions to `ConnectionStatus`:**
```typescript
type ConnectionStatus =
  | "connected"
  | "reconnecting"
  | "recovered"
  | "expired";
```

**When to pull history instead of resume:**
- Browser tab was fully closed (no `sessionStorage` lastSeq)
- Reconnect attempt rejected (session already ended)
- Manual refresh without pending agent task

### Backend: WebSocket Changes

**New incoming message type:**
```json
{ "type": "resume", "session_id": "sess_xxx", "last_seq": 42 }
```

**Processing:**
1. Look up session in `MessageBuffer` and running tasks dict
2. If session has an active agent task → replay buffered messages from `seq > last_seq` onward
3. If session task has ended → send a `state_change: done` or `state_change: error` followed by buffered messages after last_seq
4. If session unknown → reply `{ type: "resume_error", reason: "session_not_found" }`

**Outgoing message seq field:**
Every WS message gains an integer `seq` field, monotonically increasing per-session.
The `MessageBuffer` already tracks messages in order — seq is just a cursor on top.

### Edge Cases

- **Agent task still running but all messages after last_seq already sent**: send a heartbeat to confirm connection is live
- **Resume during a tool call**: replay the `tool_call_start` if last_seq < tool seq, so the UI can show it as in-progress
- **Multiple resume attempts**: idempotent — second resume with same last_seq gets same replay

---

## Module 2: Error Severity Classification + Recovery Actions

### Goal

Every error the user sees should tell them three things:
1. What happened (in plain language)
2. How severe it is (can I keep going?)
3. What I can do about it (a button, not just a message)

### Severity Levels

| Level | Meaning | Color | User Can Fix? |
|-------|---------|-------|---------------|
| `critical` | System-level, admin action required | Red | No — contact support |
| `retryable` | Transient failure, agent can recover | Yellow/amber | Yes — retry or simplify |
| `actionable` | User input caused the problem | Blue | Yes — specific guidance |

### Backend: `handle_task_error` Changes

Current error message:
```json
{ "type": "error", "message": "raw error string" }
```

New structure:
```json
{
  "type": "error",
  "message": "Agent process disconnected unexpectedly.",
  "severity": "retryable",
  "detail": "ProcessTransport is not ready for writing. The CLI subprocess may have been killed.",
  "actions": [
    { "label": "Retry with new session", "kind": "new_session" },
    { "label": "Copy error details", "kind": "copy_detail" }
  ]
}
```

**Severity classification rules:**

| Exception Pattern | Severity | Default Action |
|-------------------|----------|----------------|
| `CLIConnectionError`, `ConnectionError` | `retryable` | `new_session` |
| Buffer overflow (JSON max size exceeded) | `retryable` | `simplify` |
| `TimeoutError` | `retryable` | `retry` |
| `asyncio.CancelledError` (user cancel) | N/A — handled separately | N/A |
| Auth / permission / key errors | `critical` | `copy_detail` |
| File too large, path rejected, rate limited | `actionable` | Varies by cause |

### Frontend: `ErrorCard` Component

New component extracted from `MessageBubble`, replaces the current `<div className="error">` rendering:

```
┌──────────────────────────────────────────┐
│ 🔴 Connection failed                      │
│                                          │
│ Agent process disconnected unexpectedly.  │
│                                          │
│ [Start new session] [Copy error details]  │
│                                          │
│ ▶ Show details                           │  ← expandable
│   ProcessTransport is not ready for...    │
└──────────────────────────────────────────┘
```

**States:**
- Critical (red): banner icon + message + detail copy action
- Retryable (amber): banner + message + retry/simplify/new-session actions + auto-retry count indicator
- Actionable (blue): banner + message + context-specific action button

**Auto-retry indicator** (retryable only):
```
Retrying automatically... (attempt 1/2)
[Cancel retry]
```

Note: the actual retry is already triggered by the backend in `LocalAgentExecutor.run` (the
CLIConnectionError handler). The UI just reflects the retry status. If the backend retries
and succeeds, the error is replaced by a recovery notice.

---

## Module 3: Tool-Call Visualization Enhancement

### Goal

Users understand what the agent is doing, how long each step takes, and can navigate
results efficiently — without overwhelming the chat view.

### Sub-module 3a: Tool Card Redesign

Current: tool name + raw content block in `MessageBubble`.

New `ToolCard` component (extracted from `MessageBubble`):

```
┌──────────────────────────────────────────┐
│ 🔧 Run bash command           ⏱ 3.2s     │
│                                          │
│ $ npm test -- --coverage                 │  ← truncated, expandable
│                                          │
│ ✓ 142 passed, 3 skipped                  │  ← 1-2 line result summary
│                                          │
│ [Expand output] [Copy command]           │
└──────────────────────────────────────────┘
```

**Tool-specific result summaries:**

| Tool | Summary Format |
|------|---------------|
| `Bash` | Exit code + first meaningful line of stdout |
| `Write` | `+N -M lines in path/to/file` |
| `Read` | File count + total bytes |
| `Grep` | Match count |
| `WebSearch` / `WebFetch` | Title or first sentence |
| Generic / MCP | Truncated first 200 chars |

**Duration display:**
- Backend already provides `duration_ms` in `tool_call_end` events
- Display: `< 1s` → gray, `< 5s` → default, `> 10s` → warning color

### Sub-module 3b: Tool Group Merging

When the agent executes a batch of same-tool calls consecutively, merge into a group card:

```
┌──────────────────────────────────────────┐
│ 📂 Read 8 files               ⏱ 2.1s     │
│                                          │
│ src/agent/local.py                        │
│ src/agent/container.py                    │
│ src/event_pipeline.py                     │
│ ... and 5 more                           │
│                                          │
│ [Expand all]                              │
└──────────────────────────────────────────┘
```

**Merge rule:** Consecutive tool calls of the same `tool_name` that appear without
interleaving user/assistant text messages.

### Sub-module 3c: Progress Bar

A thin progress indicator at the top of the chat area showing the agent's current phase:

```
[Analyze] ──► [Read files] ──► [Edit code] ──► [Verify]
   ✅             🔄              ⏳              ⏳
```

**Heuristic for phase detection:**
- First N turns with predominantly Read/Grep → "Analyze"
- First Write/Edit tool call → "Edit code"
- First Bash/Test tool call after edits → "Verify"
- Default → "Working"

This is a best-effort heuristic displayed as a subtle indicator, not a guaranteed state machine.

### Frontend Implementation Notes

`MessageBubble.tsx` is currently ~28KB. The work should:

1. Extract `ToolCard` as `src/components/ToolCard.tsx` — handles single tool call rendering
2. Extract `ToolGroupRenderer` as `src/components/ToolGroupRenderer.tsx` — handles merging logic
3. Extract `ErrorCard` (module 2) as `src/components/ErrorCard.tsx`
4. Add `ProgressBar` as `src/components/ProgressBar.tsx`
5. `MessageBubble` becomes thinner, delegating to these sub-components

`ChatArea.tsx` (~17KB) owns the progress bar state derived from message list analysis.

---

## Implementation Order

| Order | Module | Rationale |
|-------|--------|-----------|
| 1 | Error classification (backend) | Simplest backend change, unblocks frontend ErrorCard |
| 2 | ErrorCard + severity UI (frontend) | Immediate UX win, builds on module 1 backend |
| 3 | ToolCard extraction | Refactor before adding features — safe, no behavior change |
| 4 | Tool group merging | Builds on extracted ToolCard |
| 5 | Progress bar | Lightweight, builds on tool event analysis |
| 6 | WebSocket reconnect + resume | Most complex, save for last after frontend components stabilized |

Modules 1-2 can ship as one PR, modules 3-5 as a second PR, module 6 as a third PR.

---

## Design Decisions

### Why not use a full state machine library (XState)?
The reconnect logic is simple enough (4 states, 3 transitions) that a reducer in `useWebSocket` is sufficient. A library adds dependency weight without proportional benefit.

### Why heuristic phase detection instead of agent-reported phases?
The agent SDK doesn't expose phase metadata. Asking the model to self-report phases would consume tokens and be unreliable. A heuristic on tool call patterns is zero-cost and correct ~80% of the time, which is enough for a subtle indicator.

### Why merge only consecutive same-tool calls?
Non-consecutive merges (e.g., grouping all Read calls across the entire session) would lose temporal context — the order of operations matters for understanding agent behavior.

### Backward compatibility
- **Error messages**: Old `{ "type": "error", "message": "..." }` format continues to render — frontend detects missing `severity` field and falls back to generic "error" display
- **WebSocket without seq**: If a server hasn't been upgraded yet, the frontend treats missing `seq` as seq=0 and falls back to full history pull on reconnect
- **Tool cards**: Old tool calls without `duration_ms` simply hide the duration label

# Request Latency Optimization — Design

## Problem

User message → first character displayed takes 10s+. Every message
(including subsequent ones in the same session) repeats full setup work.

## Root Cause Analysis

Per-message hot path in `run_agent_task`:

| Stage | Latency | Repeated per message? |
|-------|---------|-----------------------|
| `load_skills()` — disk scan | 100-500ms | Yes |
| `_load_wiki_context()` — blocking event loop + DB | 100-500ms | Yes, always returned "" |
| `_load_semantic_context()` — blocking event loop + DB | 100-500ms | Yes, always returned "" |
| `build_system_prompt()` — string assembly | 50-200ms | Yes |
| `client.connect()` — CLI subprocess spawn | 300ms-2s | Yes |
| Model TTFT (DeepSeek) | 3-8s | Yes |

The setup overhead (2-5s) before the model even sees the prompt is pure
waste for subsequent messages.

## Design

### Part 1: Dead Code Removal (done)

- `_load_wiki_context` — function + call site removed. Wiki pages were never
  auto-published, so the query always returned empty.
- `_load_semantic_context` — converted to `async def` (await instead of
  blocking event loop), call site removed with re-enable marker comment.
  Session summary pipeline needs work before this is useful.

### Part 2: CLI Subprocess Reuse

Transform `run_agent_task` from a per-message one-shot coroutine into a
per-session long-lived coroutine:

```
Session start → client.connect() → CLI subprocess spawned ONCE
    ↓
while session alive:
    wait for next user message (chat / answer / cancel)
    if current query running → cancel it
    client.query(prompt) → receive_response() → stream to frontend
    ↓
Session end / WS disconnect → SIGTERM → subprocess cleanup
```

Key changes:
- `handle_ws` sends messages to `run_agent_task` via `asyncio.Queue`
- `run_agent_task` becomes a long-running coroutine with an inner loop
- CLI subprocess lives for the session duration, not per-message
- Cancel support: interrupt current stream when user sends new message

### Part 3: Prompt & Skills Caching

- Skills: load once per `handle_ws` session, re-scan only on directory mtime change
- System prompt: build once per session, rebuild only on language/skills/wiki change
- Both passed as cached values to `run_agent_task`

## Expected Impact

| Scenario | Before | After |
|----------|--------|-------|
| First message (new session) | 6-13s | 6-13s (first subprocess + prompt build unavoidable) |
| Subsequent messages | 6-13s | 3-8s (only model TTFT remains) |
| Interrupt + resend | 6-13s | 2-7s (subprocess alive, cancel old query) |

## Non-Goals

- Container mode CLI reuse (same pattern, separate implementation)
- Model TTFT reduction (depends on model provider)
- Prompt caching at model API level (DeepSeek-specific)
- Frontend optimizations (optimistic rendering already in place)

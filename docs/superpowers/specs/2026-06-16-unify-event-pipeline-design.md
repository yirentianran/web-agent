# Unify Event Pipeline: Container & Non-Container Modes

## Problem

The container mode (`container_bridge.py`) and non-container mode (`main_server.py`
main loop) each implement the same event-processing logic independently:

- `process_content_blocks` for assistant / user message content blocks
- `maybe_truncate_tool_result_content` for oversized results
- `ToolObserver` recording (on_tool_use, on_tool_result)
- Skill-usage tracking (`record_skill_usage_from_event`)
- Message-buffer writes

Duplication causes drift: `tool_use_names` is not shared across messages in
container mode, user messages can be silently dropped when the `message` field is
missing, Write-file tracking is absent from container mode, and any fix applied to
one pipeline must be manually ported to the other.

The post-loop logic (file scan, file_result emission, title generation, session
completion, mark_done, observation summary) is also duplicated between
`run_agent_task` and `run_agent_task_container`.

## Goal

Single event-processing pipeline. The container bridge becomes a thin transport
layer — it connects to the agent-server WebSocket, receives JSON, and feeds raw
dicts into the shared pipeline. Both modes use the same functions for event
conversion, per-event processing, and post-loop teardown.

## Design

### Architecture

```
Non-container:  SDK objects → message_to_dicts() ─┐
                                                    ├→ process_event() → buffer → frontend
Container:      WS JSON ────→ message_to_dicts() ──┘
                    ↑
            container_bridge
            (transport only)
```

Three shared components, all located in a new file `src/event_pipeline.py`:

| Component | Responsibility |
|-----------|---------------|
| `message_to_dicts` (extended) | Convert SDK objects **and** WS JSON dicts → standard event dicts |
| `process_event(ctx, event)` | Per-event: skip / truncate / observe / track / buffer |
| `_finish_task(...)` | Post-loop: file scan, file_result, title, completion, mark_done |

### 1. `message_to_dicts` — Add `isinstance(msg, dict)` Branch

Current function dispatches on SDK dataclass types (`UserMessage`,
`AssistantMessage`, `StreamEvent`, `ResultMessage`, …). Add one branch at the
top for plain dicts arriving from the container bridge.

Container WS dicts mirror the SDK types:

| WS dict | Equivalent SDK type |
|---------|---------------------|
| `{"type":"assistant","message":{"content":[...]}}` | `AssistantMessage` |
| `{"type":"user","message":{"content":[...]}}` | `UserMessage` |
| `{"type":"stream_event","event":{...}}` | `StreamEvent` |
| `{"type":"result",...}` | `ResultMessage` |

The dict branch extracts `message.content` for assistant / user, calls
`process_content_blocks` (passing the shared `tool_use_names`), and yields the
same dict format the SDK-object branches produce. For stream_event it yields the
standard wrapper; for result it calls `parse_agent_result`.

No other changes to `message_to_dicts`. Existing SDK-object branches are untouched.

### 2. `process_event(ctx, event)` — Shared Per-Event Handler

Extracted from the non-container main loop (`main_server.py` lines 2413–2489).
Accepts an `EventContext` dataclass and a single event dict.

```python
@dataclass(frozen=True)
class EventContext:
    user_id: str
    session_id: str
    buffer: MessageBuffer
    observer: ToolObserver | None
    skill_manager: Any | None
    generated_files: list[dict]
```

Processing order (preserved from current non-container loop):

1. **Skip** `user`-type events (user message already persisted before task start)
2. **Skip** `AskUserQuestion` tool_use (handled by permission_check in bridge, or
   `_can_use_tool_for_session` in non-container mode)
3. **Record skill usage** if `skill_manager` is set
4. **Track Write files** — append to `ctx.generated_files` for later `file_result`
   emission (previously missing from container mode)
5. **Truncate** oversized tool_result content
6. **Write to buffer**
7. **Record observation** (tool_use → `on_tool_use`, tool_result → `on_tool_result`)

Both caller loops reduce to:

```python
for event in message_to_dicts(msg, model=model, tool_use_names=tool_use_names):
    if event.get("type") == "result":
        buffered_result = event
        continue
    await process_event(ctx, event)
```

### 3. Container Bridge — Transport Only

The bridge keeps exactly what belongs to it:

- WebSocket connect / receive / send
- `permission_check` → AskUserQuestion bidirectional handling
- `done` / `error` / `cancelled` terminal signals
- Accumulated text for synthetic assistant fallback

Everything else is removed: no `process_content_blocks` calls, no
`buffer.add_message` calls, no `ToolObserver` calls, no `record_skill_usage_from_event`
calls, no msg_type dispatch on `assistant` / `user` / `stream_event` / `result`.

Bridge now accepts `ctx`, `model`, and `tool_use_names` so it can feed events into
the shared pipeline:

```python
# container_bridge.py — core loop after refactor
while True:
    data = await asyncio.wait_for(self._receive_queue.get(), timeout=30)

    if data.get("type") == "permission_check":
        await self._handle_permission_check(data, ctx)
        continue

    if data.get("type") in ("done", "error", "cancelled"):
        # terminal handling (unchanged)
        break

    for event in message_to_dicts(data, model=model, tool_use_names=tool_use_names):
        await process_event(ctx, event)
```

`_handle_permission_check` uses `process_event` for the buffer write (AskUserQuestion
tool_use), keeping only the future-registration + answer-forwarding logic.

### 4. `_finish_task(...)` — Shared Post-Loop Teardown

Extracted from the duplicated ~30 lines at the end of `run_agent_task` and
`run_agent_task_container`. Covers:

1. `_scan_workspace_for_generated_files`
2. `_emit_file_result`
3. `_auto_generate_title`
4. `buffer.add_message(session_state_changed: completed)`
5. `buffer.add_message(result_event)` (reordered after file_result + completed)
6. `buffer.mark_done`
7. `agent_log.end_session`
8. `obs_store.record(session_complete)`
9. `_summarize_and_store_session` (background)
10. `skill_manager.migrate_from_filesystem` (background)

Non-container passes `buffered_result`; container passes `bridge._result`.

### 5. Error / Cancel Branches

Error and cancel handling remain in their respective task functions — the
exception types and cancellation mechanisms differ between modes. Both paths use
`process_event` for error-message buffer writes and `_finish_task` for teardown
(with `result_event=None`).

## File Changes

| File | Change |
|------|--------|
| `src/event_pipeline.py` | **New** — `EventContext`, `process_event`, `_finish_task` |
| `main_server.py` | `message_to_dicts` gains `isinstance(msg, dict)` branch; both `run_agent_task*` functions simplified to call `process_event` + `_finish_task` |
| `src/container_bridge.py` | Remove ~80 lines of event processing; bridge accepts `ctx` / `model` / `tool_use_names`; core loop delegates to `message_to_dicts` + `process_event`; `_handle_permission_check` uses `process_event` for buffer write |

## Testing

| Target | Type | Notes |
|--------|------|-------|
| `message_to_dicts` dict branch | Unit | Construct assistant / user / stream_event / result dicts; assert yielded events match expectations |
| `process_event` | Unit | Mock `EventContext`; verify skip logic, truncation call, obs recording per event type |
| `_finish_task` | Integration | Mock dependencies; verify call order |
| Non-container regression | Existing suite | SDK-object branches untouched |
| Container end-to-end | Integration | Mock agent-server WS; feed JSON; assert buffer contents |

## Risks

- **Low risk** — `message_to_dicts` SDK-object branches are not modified; non-container
  regression surface is minimal.
- **Medium risk** — Container bridge refactor touches the hot path. Mitigated by
  keeping the transport layer (`_receive_loop`, `connect`, `send_run`) untouched
  and only changing the event-dispatch logic.
- **Rollback** — If container issues arise, the old bridge logic can be restored
  independently since the shared pipeline components are new files / additive.

# Container / Non-Container Mode Refactor — Design Spec

**Date:** 2026-06-18  
**Status:** Approved  
**Scope:** Full refactor — unify container and non-container agent execution paths

## Motivation

The codebase evolved in phases (direct SDK → container isolation) without an
overall design. This produced:

- Two parallel agent execution functions (`run_agent_task`, `run_agent_task_container`)
  with ~700 lines of near-duplicate scaffolding
- Two options builders (`build_sdk_options`, `build_container_options_dict`) that
  share `_build_sdk_config` but diverge on SDK hooks vs dict serialization
- Three near-identical error-handling blocks (TimeoutError, CancelledError, Exception)
- Security hooks (Write path, Bash filter, Read limits) implemented twice:
  once as SDK callbacks in `main_server.py`, once as control_request handlers in
  `agent_server.py`
- `main_server.py` grown to ~6900 lines with deeply interleaved mode branches
- Dual handling in `message_to_dicts()` and `process_content_blocks()` for
  SDK dataclass types vs unstructured JSON dicts
- `agent_server.py` reimplements CLI subprocess management that mimics SDK internals

## Target Module Structure

```
src/
├── agent/                              # NEW: Agent execution abstraction
│   ├── __init__.py
│   ├── protocol.py                     # Typed internal event types
│   ├── options.py                      # Unified options builder
│   ├── prompt.py                       # Prompt building (history, language, attachments)
│   ├── local.py                        # LocalAgentExecutor (ClaudeSDKClient wrapper)
│   ├── container.py                    # ContainerAgentExecutor (ContainerBridge wrapper)
│   └── adapters/
│       ├── __init__.py
│       ├── sdk.py                      # SDK dataclass → InternalEvent
│       └── container_json.py           # Container JSON dict → InternalEvent
├── security/
│   ├── __init__.py
│   ├── filters.py                      # OutputFilter, BashCommandFilter, FileAccessFilter (migrated)
│   ├── enforcer.py                     # SecurityEnforcer — shared pre-execution checks
│   └── rate_limiter.py                 # ToolCallRateLimiter (extracted)
├── event_pipeline.py                   # Simplified — consumes InternalEvent
├── message_buffer.py                   # (unchanged)
├── session_store.py                    # (unchanged)
├── container_bridge.py                 # Thinned — delegates adaptation to src/agent/adapters
├── container_manager.py                # (unchanged)
├── workspace_enforcement.py            # (unchanged)
```

## Design Decisions

### 1. Internal Protocol Layer (`src/agent/protocol.py`)

**Choice:** Discriminated union of frozen dataclasses for internal events.

Both modes convert their raw output (SDK dataclasses or container JSON dicts)
into typed `InternalEvent` instances. The entire downstream pipeline
(`event_pipeline.py`, `process_event`, `_finish_task`) operates on
`InternalEvent` only — never on raw dicts.

Event types:

```
AssistantEvent  — text content from agent
ToolUseEvent    — tool invocation (name, id, input)
ToolResultEvent — tool execution result
StreamEvent     — streaming delta from SDK
SystemEvent     — lifecycle notifications (timeout, cancel, progress, session_state_changed)
ResultEvent     — final agent result (usage, stop_reason, duration)
ErrorEvent      — error messages (subtype: timeout, cancelled, general)
```

`InternalEvent = AssistantEvent | ToolUseEvent | ToolResultEvent | StreamEvent | SystemEvent | ResultEvent | ErrorEvent`

For WebSocket serialization to the frontend, each event type has a `to_dict()`
method that produces the existing dict format. This ensures zero frontend
changes — the wire format is unchanged.

For the `MessageBuffer`, events are stored as dicts (same as today) via
`event.to_dict()` at the persistence boundary.

### 2. Adapters (`src/agent/adapters/`)

Two thin adapter modules, each exporting one function:

**`sdk.py`** — `adapt_sdk_message(msg: Any, ...) -> Iterator[InternalEvent]`
- Handles: `AssistantMessage`, `UserMessage`, `ResultMessage`, `TaskNotificationMessage`,
  `TaskProgressMessage`, `SystemMessage`, `StreamEvent`
- Delegates content block processing to `process_content_blocks()`

**`container_json.py`** — `adapt_container_message(data: dict, ...) -> Iterator[InternalEvent]`
- Handles: `{"type": "assistant", ...}`, `{"type": "user", ...}`,
  `{"type": "stream_event", ...}`, `{"type": "result", ...}`
- Same content block delegation

Both adapters share `process_content_blocks()` from `src/block_processor.py`,
which already handles both SDK dataclass blocks and JSON dict blocks. The
dual-path in that function is acceptable — it's a leaf function handling a
single concern (content block → emitted events).

This replaces the current `message_to_dicts()` (lines 1759–1878) which has
interleaved branches for SDK types and dicts.

### 3. AgentExecutor Protocol (`src/agent/`)

```python
class AgentExecutor(Protocol):
    async def run(
        self,
        prompt: str,
        options: AgentOptions,
        ctx: EventContext,
    ) -> AgentRunResult:
        ...
```

Two implementations:

**`LocalAgentExecutor`** (`src/agent/local.py`)
- Wraps `ClaudeSDKClient`
- Manages client lifecycle: connect, query, receive_response, reconnect on
  CLIConnectionError
- Iterates `client.receive_response()`, feeds each message through
  `adapt_sdk_message()`, emits `InternalEvent` to `process_event()`
- Caches client in `session_agents[sid]["client"]`

**`ContainerAgentExecutor`** (`src/agent/container.py`)
- Wraps `ContainerBridge`
- Manages bridge lifecycle: ensure_container, connect, run_and_stream,
  reconnect on ConnectionError
- Receives JSON dicts from bridge, feeds through `adapt_container_message()`,
  emits `InternalEvent` to `process_event()`
- Caches bridge in `session_agents[sid]["bridge"]`

**Shared run loop** lives in `src/agent/_run_loop.py` as a helper consumed by
both executors. It handles the receive → adapt → process_event cycle. Error
handling (TimeoutError, CancelledError, Exception) is extracted to one shared
`_handle_task_error()` in `event_pipeline.py`. Like `_finish_task()`, it
receives all module-level singletons (`buffer`, `_obs_store`, `agent_log`,
`session_id`, `user_id`) as explicit parameters to avoid circular imports.

### 4. Unified Options Builder (`src/agent/options.py`)

Merges `build_sdk_options()` (line 1542) and `build_container_options_dict()`
(line 1494) into a single builder that returns a `AgentOptions` dataclass:

```python
@dataclass(frozen=True)
class AgentOptions:
    model: str
    system_prompt: str
    allowed_tools: list[str]
    disallowed_tools: set[str]
    max_turns: int
    mcp_servers: dict | None
    env: dict | None
    include_partial_messages: bool
    max_buffer_size: int
    permission_mode: str
    cwd: str | None  # container-only
    resume_session_id: str | None
```

The builder calls shared `_build_sdk_config()` (unchanged). `LocalAgentExecutor`
converts `AgentOptions` to `ClaudeAgentOptions` internally; `ContainerAgentExecutor`
serializes `AgentOptions` to dict for WebSocket transmission. The SDK hook
construction (Write path, Bash) moves to `LocalAgentExecutor`, calling
`SecurityEnforcer`.

### 5. SecurityEnforcer (`src/security/enforcer.py`)

Extracts shared pre-execution security logic:

```python
@dataclass
class SecurityEnforcer:
    user_id: str
    workspace: Path
    user_dir: Path

    def check_bash(self, command: str) -> tuple[bool, str]: ...
    def check_write_path(self, file_path: str) -> tuple[bool, str]: ...
    def check_read_path(self, file_path: str) -> tuple[bool, str]: ...
```

**Non-container mode:** `LocalAgentExecutor` builds SDK `can_use_tool` and
`PreToolUse` hooks that delegate to `SecurityEnforcer`.

**Container mode:** `agent_server.py`'s `_CliRunner` control_request handler
imports and calls `SecurityEnforcer` directly. The entire `src/` directory is
already copied into the container image via `Dockerfile.user`, so the import
works without changes to the Docker build.

Existing `BashCommandFilter`, `FileAccessFilter`, `OutputFilter` classes move
from `src/security_filter.py` to `src/security/filters.py` with no API changes.
`ToolCallRateLimiter` extracts to `src/security/rate_limiter.py`.

### 6. `main_server.py` Slim-Down

Target: ~6900 lines → ~4000 lines.

**Removed and migrated out:**
- `run_agent_task()` (lines 2279–2646) → `src/agent/local.py`
- `run_agent_task_container()` (lines 2650–2930) → `src/agent/container.py`
- `build_sdk_options()` (lines 1542–1757) → merged into `src/agent/options.py`
- `build_container_options_dict()` (lines 1494–1539) → merged into `src/agent/options.py`
- Inline hook closures (`write_path_hook`, `bash_path_hook`) → `SecurityEnforcer` + `LocalAgentExecutor`
- `_build_history_prompt()`, `_format_first_message_prompt()` → `src/agent/prompt.py`
- `cleanup_session_client()` → `src/agent/local.py`

**Kept:**
- REST API endpoints (sessions, files, skills, MCP, admin)
- Auth (JWT, CSRF, admin)
- WebSocket handler (`handle_ws()`)
- Module-level singletons (`buffer`, `session_store`, `_db`, etc.)
- `_build_sdk_config()` — already clean shared config builder

**Mode dispatch** in `handle_ws()` becomes:

```python
executor = (
    ContainerAgentExecutor(...) if CONTAINER_MODE
    else LocalAgentExecutor(...)
)
await executor.run(prompt, options, ctx)
```

### 7. `agent_server.py` Slim-Down

- Remove `_apply_write_path_hook()` → use `SecurityEnforcer.check_write_path()`
- Remove duplicated Bash filtering → use `SecurityEnforcer.check_bash()`
- Remove duplicated Read size enforcement → use `SecurityEnforcer.check_read_path()`
- `_CliRunner` keeps CLI subprocess management but delegates security to
  `SecurityEnforcer`

## Files Created / Modified

### New Files

| File | Purpose |
|------|---------|
| `src/agent/__init__.py` | Package init |
| `src/agent/protocol.py` | InternalEvent types |
| `src/agent/options.py` | Unified AgentOptions builder |
| `src/agent/prompt.py` | Prompt formatting helpers |
| `src/agent/local.py` | LocalAgentExecutor |
| `src/agent/container.py` | ContainerAgentExecutor |
| `src/agent/_run_loop.py` | Shared receive→adapt→process_event loop |
| `src/agent/adapters/__init__.py` | Adapter package |
| `src/agent/adapters/sdk.py` | SDK → InternalEvent |
| `src/agent/adapters/container_json.py` | JSON dict → InternalEvent |
| `src/security/__init__.py` | Security package |
| `src/security/filters.py` | OutputFilter, BashCommandFilter, FileAccessFilter |
| `src/security/enforcer.py` | SecurityEnforcer |
| `src/security/rate_limiter.py` | ToolCallRateLimiter |

### Modified Files

| File | Changes |
|------|---------|
| `main_server.py` | Remove migrated functions, delegate to executors |
| `agent_server.py` | Replace inline hooks with SecurityEnforcer |
| `src/event_pipeline.py` | Update `process_event()` to accept InternalEvent; add `_handle_task_error()` |
| `src/container_bridge.py` | Replace `message_to_dicts()` calls in `run_and_stream()` with `adapt_container_message()` from `src/agent/adapters/container_json.py` |
| `src/block_processor.py` | Minor — keep existing dual-path, may simplify later |
| `src/message_buffer.py` | No API changes needed |

### Deleted

| File | Reason |
|------|--------|
| `src/security_filter.py` | Split into `security/filters.py`, `security/enforcer.py`, `security/rate_limiter.py` |

### Unchanged

`src/session_store.py`, `src/database.py`, `src/auth.py`, `src/admin_auth.py`,
`src/cost.py`, `src/observation.py`, `src/instinct_extractor.py`,
`src/container_manager.py`, `src/mcp_store.py`, `src/skill_manager.py`,
`src/workspace_enforcement.py`, `src/agent_logger.py`, `src/semantic_search.py`,
`src/file_utils.py`, `src/truncation.py`, `frontend/` (all files)

## Backward Compatibility

- **Frontend:** Zero changes. InternalEvent.to_dict() produces identical wire
  format to current `message_to_dicts()` output.
- **MessageBuffer / SessionStore:** No schema changes. Events are stored as
  dicts identical to today.
- **REST API:** No endpoint changes.
- **Environment variables:** No changes. `CONTAINER_MODE` behaves identically.

## Testing Strategy

1. **Unit tests for protocol/adapters** — verify each adapter correctly converts
   its input type to `InternalEvent`, covering all message types from both SDK
   and container JSON paths.
2. **Unit tests for SecurityEnforcer** — verify shared enforcement logic
   produces consistent results for both modes.
3. **Unit tests for AgentOptions builder** — verify merged builder produces
   correct output for local and container consumption.
4. **Integration tests** — existing test suite covers WebSocket message flow.
   Both `CONTAINER_MODE=false` and `CONTAINER_MODE=true` paths must pass.
5. **Existing E2E tests** — unchanged, serve as regression safety net.

Existing tests in `tests/unit/` and `frontend/src/**/*.test.tsx` must continue
to pass at each step.

## Non-Goals

- Changing the container orchestration model (Docker remains)
- Changing the Wire format between browser and server
- Unifying `process_content_blocks()` dual-path (acceptable leaf-function pattern)
- Refactoring `agent_server.py`'s `_CliRunner` to use SDK directly (separate effort)
- Changing any REST API
- Changing frontend

## Risks

| Risk | Mitigation |
|------|------------|
| Large move breaks something subtle | Each file migration is a separate commit; run full test suite between commits |
| Adapter produces subtly different dict shape | Freeze existing dict output as test fixtures before refactoring |
| SecurityEnforcer behavior diverges between modes | Single implementation, tested once; both consumers call same code |
| Circular imports from new package structure | Lazy imports at call sites (already in use for MessageBuffer) |

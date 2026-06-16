# Unify Event Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate duplicated event-processing logic between container and non-container modes by creating a shared pipeline — the container bridge becomes a thin transport layer.

**Architecture:** Add `isinstance(msg, dict)` branch to `message_to_dicts` so it accepts container WS JSON dicts. Extract per-event processing into `process_event()` and post-loop teardown into `_finish_task()` in a new `src/event_pipeline.py`. Strip duplicate logic from `container_bridge.py`.

**Tech Stack:** Python (FastAPI), claude_agent_sdk, pytest

---

## File Structure

| File | Role |
|------|------|
| `src/event_pipeline.py` | **New** — `EventContext`, `process_event`, `_finish_task` |
| `main_server.py` | Modify — `message_to_dicts` gains dict branch; both task functions use shared pipeline |
| `src/container_bridge.py` | Modify — strip event processing; delegate to `message_to_dicts` + `process_event` |
| `tests/unit/test_event_pipeline.py` | **New** — unit tests for dict branch, `process_event`, `_finish_task` |

---

### Task 1: Create `EventContext` dataclass and `process_event` function

**Files:**
- Create: `src/event_pipeline.py`
- Create: `tests/unit/test_event_pipeline.py`

- [ ] **Step 1: Write failing tests for `process_event`**

```python
# tests/unit/test_event_pipeline.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.event_pipeline import EventContext, process_event


class TestProcessEvent:
    @pytest.fixture
    def ctx(self):
        return EventContext(
            user_id="u1",
            session_id="s1",
            buffer=AsyncMock(),
            observer=AsyncMock(),
            skill_manager=None,
            generated_files=[],
        )

    async def test_skips_user_type_events(self, ctx):
        await process_event(ctx, {"type": "user", "content": "hello"})
        ctx.buffer.add_message.assert_not_called()

    async def test_records_skill_usage_when_manager_set(self, ctx):
        from unittest.mock import patch

        ctx = EventContext(
            user_id="u1", session_id="s1",
            buffer=AsyncMock(), observer=AsyncMock(),
            skill_manager=MagicMock(), generated_files=[],
        )
        with patch("src.event_pipeline.record_skill_usage_from_event", new_callable=AsyncMock) as mock_rec:
            await process_event(ctx, {"type": "tool_use", "name": "Skill", "id": "id1", "input": {}})
            mock_rec.assert_called_once()

    async def test_truncates_oversized_tool_result(self, ctx):
        long_content = "x" * 200_000
        with patch("src.event_pipeline.maybe_truncate_tool_result_content") as mock_trunc:
            mock_trunc.return_value = "truncated"
            await process_event(ctx, {"type": "tool_result", "tool_use_id": "tu1", "content": long_content})
            # Verify truncation was called before buffer write
            mock_trunc.assert_called_once_with(long_content)
            call_args = ctx.buffer.add_message.call_args
            assert call_args[0][1]["content"] == "truncated"

    async def test_writes_event_to_buffer(self, ctx):
        event = {"type": "assistant", "content": "hello"}
        await process_event(ctx, event)
        ctx.buffer.add_message.assert_called_once_with("s1", event, "u1")

    async def test_records_tool_use_observation(self, ctx):
        event = {"type": "tool_use", "name": "Bash", "id": "id1", "input": {"cmd": "ls"}, "seq": 5}
        await process_event(ctx, event)
        ctx.observer.on_tool_use.assert_called_once_with("id1", "Bash", {"cmd": "ls"}, message_seq=5)

    async def test_records_tool_result_observation(self, ctx):
        event = {"type": "tool_result", "tool_use_id": "tu1", "content": "ok", "is_error": False}
        await process_event(ctx, event)
        ctx.observer.on_tool_result.assert_called_once_with("tu1", is_error=False)

    async def test_skips_ask_user_question_tool_use(self, ctx):
        event = {"type": "tool_use", "name": "AskUserQuestion", "id": "id1", "input": {}}
        await process_event(ctx, event)
        ctx.buffer.add_message.assert_not_called()

    async def test_tracks_write_file_in_generated_files(self, ctx):
        event = {
            "type": "tool_use", "name": "Write",
            "id": "id1",
            "input": {"file_path": "outputs/s1/report.txt", "content": "data"},
        }
        with patch("src.event_pipeline.normalize_write_path", return_value="outputs/s1/report.txt"):
            with patch("src.event_pipeline.should_include_generated_file", return_value=True):
                with patch("src.event_pipeline.build_download_url", return_value="/dl/report.txt"):
                    await process_event(ctx, event)
        assert len(ctx.generated_files) == 1
        assert ctx.generated_files[0]["filename"] == "report.txt"

    async def test_observer_none_does_not_crash(self, ctx):
        ctx = EventContext(
            user_id="u1", session_id="s1",
            buffer=AsyncMock(), observer=None,
            skill_manager=None, generated_files=[],
        )
        await process_event(ctx, {"type": "tool_use", "name": "Bash", "id": "id1", "input": {}})
        await process_event(ctx, {"type": "tool_result", "tool_use_id": "tu1", "content": "ok"})
        # Should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/unit/test_event_pipeline.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'src.event_pipeline'`

- [ ] **Step 3: Create `src/event_pipeline.py` with `EventContext` and `process_event`**

```python
"""Shared event-processing pipeline used by both container and non-container modes.

The container bridge feeds raw WS JSON dicts into ``message_to_dicts``, which
now accepts both SDK dataclass objects and plain dicts.  Per-event processing
(truncation, observation recording, skill tracking, buffer writes) and post-loop
teardown live here so the two code paths stay in sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.block_processor import process_content_blocks
from src.truncation import maybe_truncate_tool_result_content
from src.workspace_enforcement import normalize_write_path


@dataclass(frozen=True)
class EventContext:
    """Immutable context carried through every ``process_event`` call."""

    user_id: str
    session_id: str
    buffer: Any  # MessageBuffer — avoid circular import
    observer: Any | None  # ToolObserver | None
    skill_manager: Any | None
    generated_files: list[dict] = field(default_factory=list)


async def process_event(ctx: EventContext, event: dict[str, Any]) -> None:
    """Process a single event dict: skip, truncate, track, buffer, observe.

    Called by both ``run_agent_task`` (non-container) and the container bridge
    after ``message_to_dicts`` has converted the raw message into standard
    event dicts.
    """
    etype = event.get("type", "")

    # User messages are persisted before the agent task starts; duplicates
    # from the agent response (e.g. replayed history) must be skipped.
    if etype == "user":
        return

    # AskUserQuestion is handled by _can_use_tool_for_session (non-container)
    # or _handle_permission_check (container bridge).  The tool_use has already
    # been buffered at that point, so skip the duplicate here.
    if etype == "tool_use" and event.get("name") == "AskUserQuestion":
        return

    # ── skill usage tracking ──────────────────────────────────────
    if ctx.skill_manager is not None:
        from src.skill_manager import record_skill_usage_from_event  # noqa: PLC0415

        await record_skill_usage_from_event(
            event, ctx.skill_manager,
            user_id=ctx.user_id, session_id=ctx.session_id,
        )

    # ── Write file tracking (generated files) ─────────────────────
    if etype == "tool_use" and event.get("name") == "Write":
        _track_write_file(event, ctx)

    # ── tool_result truncation ────────────────────────────────────
    if etype == "tool_result":
        event = {
            **event,
            "content": maybe_truncate_tool_result_content(event.get("content", "")),
        }

    # ── persist ───────────────────────────────────────────────────
    await ctx.buffer.add_message(ctx.session_id, event, ctx.user_id)

    # ── observation recording ─────────────────────────────────────
    if ctx.observer is not None:
        if etype == "tool_use":
            await ctx.observer.on_tool_use(
                event.get("id", ""),
                event.get("name", ""),
                event.get("input", {}),
                message_seq=event.get("seq"),
            )
        elif etype == "tool_result":
            await ctx.observer.on_tool_result(
                event.get("tool_use_id", ""),
                is_error=event.get("is_error", False),
            )


def _track_write_file(event: dict[str, Any], ctx: EventContext) -> None:
    """Extract file metadata from a Write tool_use and append to generated_files."""
    from src.file_utils import (  # noqa: PLC0415
        build_download_url,
        should_include_generated_file,
    )

    tool_input = event.get("input") or {}
    file_path = tool_input.get("file_path", "")
    if file_path:
        file_path = normalize_write_path(file_path, ctx.session_id)
    if not file_path or not should_include_generated_file(Path(file_path).name):
        return

    # Skill-internal files are not user-facing.
    if ".claude/skills/" in file_path or "shared-skills/" in file_path:
        return

    display_name = Path(file_path).name
    content = tool_input.get("content", "")
    try:
        size = len(content.encode("utf-8"))
    except Exception:
        size = len(content)

    download_url = build_download_url(ctx.user_id, file_path, directory="outputs")
    entry = {
        "filename": display_name,
        "size": size,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "download_url": download_url,
    }

    # Dedup: keep only the latest version for the same filename.
    dup_idx = next(
        (i for i, g in enumerate(ctx.generated_files) if g["filename"] == display_name),
        None,
    )
    if dup_idx is not None:
        ctx.generated_files[dup_idx] = entry
        return

    ctx.generated_files.append(entry)
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/unit/test_event_pipeline.py -v
```
Expected: PASS (all tests)

- [ ] **Step 5: Type-check the new module**

```
uv run mypy src/event_pipeline.py
```

- [ ] **Step 6: Commit**

```bash
git add src/event_pipeline.py tests/unit/test_event_pipeline.py
git commit -m "feat: add shared EventContext and process_event for unified event pipeline

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: Add `isinstance(msg, dict)` branch to `message_to_dicts`

**Files:**
- Modify: `main_server.py:1704-1808`
- Modify: `tests/unit/test_event_pipeline.py`

- [ ] **Step 1: Write failing tests for dict-branch of `message_to_dicts`**

Append to `tests/unit/test_event_pipeline.py`:

```python
class TestMessageToDictsDictBranch:
    """Tests for isinstance(msg, dict) branch in message_to_dicts."""

    def test_assistant_dict_yields_tool_use_and_text(self):
        from main_server import message_to_dicts

        msg = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me check."},
                    {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}},
                ],
            },
        }
        results = list(message_to_dicts(msg))
        types = [r["type"] for r in results]
        assert "tool_use" in types
        assert "assistant" in types
        assistant = next(r for r in results if r["type"] == "assistant")
        assert assistant["content"] == "Let me check."
        tool_use = next(r for r in results if r["type"] == "tool_use")
        assert tool_use["name"] == "Bash"

    def test_user_dict_yields_tool_result(self):
        from main_server import message_to_dicts

        msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu1", "content": "file.txt"},
                ],
            },
        }
        results = list(message_to_dicts(msg))
        assert len(results) == 1
        assert results[0]["type"] == "tool_result"
        assert results[0]["content"] == "file.txt"

    def test_stream_event_dict_yields_wrapper(self):
        from main_server import message_to_dicts

        msg = {
            "type": "stream_event",
            "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
        }
        results = list(message_to_dicts(msg))
        assert len(results) == 1
        assert results[0]["type"] == "stream_event"
        assert results[0]["event"]["delta"]["text"] == "hi"

    def test_result_dict_yields_parsed_result(self):
        from main_server import message_to_dicts

        msg = {
            "type": "result",
            "subtype": "success",
            "duration_ms": 5000,
            "num_turns": 3,
            "is_error": False,
            "result": "Done.",
        }
        results = list(message_to_dicts(msg))
        assert len(results) == 1
        assert results[0]["type"] == "result"
        assert results[0]["duration_ms"] == 5000

    def test_unknown_dict_type_is_ignored(self):
        from main_server import message_to_dicts

        msg = {"type": "unknown_xyz", "data": "abc"}
        results = list(message_to_dicts(msg))
        assert results == []

    def test_assistant_dict_without_message_field_yields_nothing(self):
        from main_server import message_to_dicts

        msg = {"type": "assistant"}
        results = list(message_to_dicts(msg))
        assert results == []

    def test_dict_branch_shares_tool_use_names(self):
        from main_server import message_to_dicts

        tool_use_names: dict[str, str] = {}
        assistant_msg = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}},
                ],
            },
        }
        list(message_to_dicts(assistant_msg, tool_use_names=tool_use_names))
        assert tool_use_names["tu1"] == "Bash"

        user_msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu1", "content": "ok"},
                ],
            },
        }
        results = list(message_to_dicts(user_msg, tool_use_names=tool_use_names))
        assert results[0]["name"] == "Bash"  # resolved from shared dict
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/unit/test_event_pipeline.py::TestMessageToDictsDictBranch -v
```
Expected: FAIL — dict branch not yet implemented; dicts fall through to the `hasattr(msg, "__dict__")` fallback.

- [ ] **Step 3: Add `isinstance(msg, dict)` branch to `message_to_dicts`**

In `main_server.py`, insert after the existing `message_to_dicts` function signature and docstring (before `if isinstance(msg, UserMessage):`):

```python
def message_to_dicts(msg: Any, model: str | None = None, tool_use_names: dict[str, str] | None = None) -> Iterator[dict[str, Any]]:
    """Convert a Claude SDK Message dataclass to one or more serializable dicts.

    ...existing docstring...
    """
    # ── Container WS JSON dict branch ──────────────────────────
    if isinstance(msg, dict):
        msg_type = msg.get("type", "")
        if msg_type == "assistant":
            message = msg.get("message", {})
            if message:
                content_blocks = message.get("content", [])
                emitted: list[dict[str, Any]] = []
                def _emit(d: dict[str, Any]) -> None:
                    emitted.append(d)
                combined_text = process_content_blocks(content_blocks, _emit, tool_use_names)
                for d in emitted:
                    yield d
                if combined_text:
                    yield {"type": "assistant", "content": combined_text}
            return
        if msg_type == "user":
            message = msg.get("message", {})
            if message:
                content_blocks = message.get("content", [])
                emitted: list[dict[str, Any]] = []
                def _emit(d: dict[str, Any]) -> None:
                    emitted.append(d)
                process_content_blocks(content_blocks, _emit, tool_use_names)
                for d in emitted:
                    yield d
            return
        if msg_type == "stream_event":
            yield {
                "type": "stream_event",
                "event": msg.get("event", {}),
            }
            return
        if msg_type == "result":
            from src.agent_result import parse_agent_result  # noqa: PLC0415
            yield parse_agent_result(msg, model=model)
            return
        # Unknown dict type — ignore
        return

    if isinstance(msg, UserMessage):
        # ... existing code unchanged ...
```

- [ ] **Step 4: Run dict-branch tests to verify they pass**

```
uv run pytest tests/unit/test_event_pipeline.py::TestMessageToDictsDictBranch -v
```
Expected: PASS

- [ ] **Step 5: Run full test suite to catch regressions**

```
uv run pytest -v
```
Expected: All existing tests still pass.

- [ ] **Step 6: Type-check**

```
uv run mypy main_server.py
```

- [ ] **Step 7: Commit**

```bash
git add main_server.py tests/unit/test_event_pipeline.py
git commit -m "feat: add isinstance(msg, dict) branch to message_to_dicts for container mode

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: Refactor `run_agent_task` (non-container) to use `process_event`

**Files:**
- Modify: `main_server.py:2401-2543`

- [ ] **Step 1: Run existing tests to establish baseline**

```
uv run pytest tests/ -v -k "agent" --tb=short
```
Expected: Record which tests pass (regression baseline).

- [ ] **Step 2: Replace non-container main loop with `process_event`**

In `main_server.py`, in `run_agent_task`, replace lines ~2401-2489 (from `tool_use_names: dict[str, str] = {}` through the end of the event loop) with:

```python
        tool_use_names: dict[str, str] = {}
        tool_observer = ToolObserver(_obs_store, session_id, user_id)
        pre_scan_snapshot = _snapshot_output_files(workspace, session_id)
        generated_files: list[dict[str, Any]] = []
        buffered_result: dict[str, Any] | None = None

        from src.event_pipeline import EventContext, process_event

        ctx = EventContext(
            user_id=user_id,
            session_id=session_id,
            buffer=buffer,
            observer=tool_observer,
            skill_manager=_skill_manager,
            generated_files=generated_files,
        )

        logger.debug("[AGENT_TASK] Starting receive_response loop")
        async for msg in client.receive_response():
            msg_count += 1
            logger.debug("[AGENT_TASK] Received message #%d: type=%s", msg_count, type(msg).__name__)
            for event in message_to_dicts(msg, model=options.model, tool_use_names=tool_use_names):
                # Buffer the SDK result message so file_result can be emitted
                # first, ensuring file cards appear before "Session completed".
                if event.get("type") == "result":
                    buffered_result = event
                    continue
                await process_event(ctx, event)
```

Also remove the now-dead local `Write` file-tracking block (lines 2433-2470) and the `tool_result` truncation block (lines 2471-2475) and the observation recording block (lines 2477-2489), since `process_event` handles them all.

- [ ] **Step 3: Run tests to verify non-container mode still works**

```
uv run pytest tests/ -v -k "agent" --tb=short
```
Expected: Same tests pass as in Step 1 baseline.

- [ ] **Step 4: Type-check**

```
uv run mypy main_server.py
```

- [ ] **Step 5: Commit**

```bash
git add main_server.py
git commit -m "refactor: use process_event in run_agent_task non-container loop

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: Create `_finish_task` shared post-loop teardown

**Files:**
- Modify: `src/event_pipeline.py`
- Modify: `tests/unit/test_event_pipeline.py`

- [ ] **Step 1: Write failing tests for `_finish_task`**

Append to `tests/unit/test_event_pipeline.py`:

```python
class TestFinishTask:
    @pytest.fixture
    def mocks(self):
        return {
            "buffer": AsyncMock(),
            "session_store": MagicMock(),
            "skill_manager": MagicMock(),
            "obs_store": AsyncMock(),
            "agent_log": MagicMock(),
        }

    @pytest.mark.asyncio
    async def test_finish_task_emits_file_result_title_and_completion(self, mocks):
        from src.event_pipeline import _finish_task

        with patch("src.event_pipeline._scan_workspace_for_generated_files", new_callable=AsyncMock) as mock_scan:
            mock_scan.return_value = []
            with patch("src.event_pipeline._emit_file_result", new_callable=AsyncMock) as mock_file:
                with patch("src.event_pipeline._auto_generate_title", new_callable=AsyncMock) as mock_title:
                    with patch("src.event_pipeline._summarize_and_store_session") as mock_sum:
                        result_event = {"type": "result", "duration_ms": 5000}
                        await _finish_task(
                            session_id="s1", user_id="u1",
                            buffer=mocks["buffer"],
                            workspace=Path("/ws"),
                            session_store=mocks["session_store"],
                            skill_manager=mocks["skill_manager"],
                            obs_store=mocks["obs_store"],
                            agent_log=mocks["agent_log"],
                            pre_scan_snapshot=set(),
                            result_event=result_event,
                            language=None,
                        )

                        # Verify order: file_result before completed
                        file_calls = mock_file.call_args_list
                        mock_title.assert_called_once()

                        # Verify completed state
                        add_msg_calls = [c[0][1] for c in mocks["buffer"].add_message.call_args_list]
                        assert any(m["type"] == "system" and m.get("state") == "completed" for m in add_msg_calls)

                        # Verify result emitted after completed
                        result_indices = [
                            i for i, c in enumerate(add_msg_calls)
                            if c.get("type") == "result"
                        ]
                        completed_indices = [
                            i for i, c in enumerate(add_msg_calls)
                            if c.get("type") == "system" and c.get("state") == "completed"
                        ]
                        if result_indices and completed_indices:
                            assert result_indices[0] > completed_indices[0]

                        mocks["buffer"].mark_done.assert_called_once_with("s1")
                        mocks["agent_log"].end_session.assert_called_once_with("s1", status="completed")

    @pytest.mark.asyncio
    async def test_finish_task_none_result_skips_result_emit(self, mocks):
        from src.event_pipeline import _finish_task

        with patch("src.event_pipeline._scan_workspace_for_generated_files", new_callable=AsyncMock) as mock_scan:
            mock_scan.return_value = []
            with patch("src.event_pipeline._emit_file_result", new_callable=AsyncMock):
                with patch("src.event_pipeline._auto_generate_title", new_callable=AsyncMock):
                    with patch("src.event_pipeline._summarize_and_store_session"):
                        await _finish_task(
                            session_id="s1", user_id="u1",
                            buffer=mocks["buffer"],
                            workspace=Path("/ws"),
                            session_store=mocks["session_store"],
                            skill_manager=mocks["skill_manager"],
                            obs_store=mocks["obs_store"],
                            agent_log=mocks["agent_log"],
                            pre_scan_snapshot=set(),
                            result_event=None,  # no result
                            language=None,
                        )
                        # result should NOT be in buffer messages
                        add_msg_calls = [c[0][1] for c in mocks["buffer"].add_message.call_args_list]
                        assert not any(c.get("type") == "result" for c in add_msg_calls)

    @pytest.mark.asyncio
    async def test_finish_task_none_skill_manager_does_not_migrate(self, mocks):
        from src.event_pipeline import _finish_task

        with patch("src.event_pipeline._scan_workspace_for_generated_files", new_callable=AsyncMock) as mock_scan:
            mock_scan.return_value = []
            with patch("src.event_pipeline._emit_file_result", new_callable=AsyncMock):
                with patch("src.event_pipeline._auto_generate_title", new_callable=AsyncMock):
                    with patch("src.event_pipeline._summarize_and_store_session"):
                        await _finish_task(
                            session_id="s1", user_id="u1",
                            buffer=mocks["buffer"],
                            workspace=Path("/ws"),
                            session_store=mocks["session_store"],
                            skill_manager=None,
                            obs_store=mocks["obs_store"],
                            agent_log=mocks["agent_log"],
                            pre_scan_snapshot=set(),
                            result_event=None,
                            language=None,
                        )
                        # skill_manager.migrate_from_filesystem should NOT be called
```

- [ ] **Step 2: Verify tests fail**

```
uv run pytest tests/unit/test_event_pipeline.py::TestFinishTask -v
```

- [ ] **Step 3: Implement `_finish_task` in `src/event_pipeline.py`**

Append to `src/event_pipeline.py`:

```python
async def _finish_task(
    session_id: str,
    user_id: str,
    buffer: Any,
    workspace: Any,
    session_store: Any,
    skill_manager: Any,
    obs_store: Any,
    agent_log: Any,
    pre_scan_snapshot: set[str],
    result_event: dict[str, Any] | None,
    language: str | None,
) -> None:
    """Post-loop teardown shared by container and non-container modes.

    Order matters:
    1. Scan workspace for newly generated files
    2. Emit file_result event
    3. Auto-generate session title
    4. Set session state to completed
    5. Emit result metadata (so footer renders after file cards + completed)
    6. Mark buffer done + end agent log
    7. Record session-complete observation + background tasks
    """
    from src.workspace_enforcement import user_workspace_dir  # noqa: PLC0415

    # 1. Scan for generated files
    from main_server import (  # noqa: PLC0415
        _auto_generate_title,
        _emit_file_result,
        _scan_workspace_for_generated_files,
        _summarize_and_store_session,
    )

    generated_files = await _scan_workspace_for_generated_files(
        workspace, user_id, session_id, exclude_paths=pre_scan_snapshot,
    )

    # 2. Emit file_result
    await _emit_file_result(user_id, session_id, workspace, generated_files, buffer)

    # 3. Generate title
    await _auto_generate_title(session_id, user_id, buffer, session_store, language)

    # 4. Session completed
    await buffer.add_message(
        session_id,
        {"type": "system", "subtype": "session_state_changed", "state": "completed"},
        user_id,
    )

    # 5. Result metadata (reordered so footer renders in order)
    if result_event is not None:
        await buffer.add_message(session_id, result_event, user_id)

    # 6. Mark done
    await buffer.mark_done(session_id)
    agent_log.end_session(session_id, status="completed")

    # 7. Observations + background tasks
    if obs_store:
        await obs_store.record(
            session_id=session_id, user_id=user_id,
            event_type="session_complete", success=True,
        )
    asyncio.ensure_future(_summarize_and_store_session(session_id, user_id))
    if skill_manager is not None:
        asyncio.ensure_future(skill_manager.migrate_from_filesystem())
```

Add `import asyncio` to the top of `src/event_pipeline.py`.

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/unit/test_event_pipeline.py::TestFinishTask -v
```

- [ ] **Step 5: Commit**

```bash
git add src/event_pipeline.py tests/unit/test_event_pipeline.py
git commit -m "feat: add _finish_task shared post-loop teardown

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: Refactor `run_agent_task` to use `_finish_task`

**Files:**
- Modify: `main_server.py:2491-2543`

- [ ] **Step 1: Replace post-loop in `run_agent_task`**

Replace the post-loop code in `run_agent_task` (lines ~2491-2543) with a call to `_finish_task`:

```python
        from src.event_pipeline import _finish_task

        await _finish_task(
            session_id=session_id,
            user_id=user_id,
            buffer=buffer,
            workspace=workspace,
            session_store=session_store,
            skill_manager=_skill_manager,
            obs_store=_obs_store,
            agent_log=agent_log,
            pre_scan_snapshot=pre_scan_snapshot or set(),
            result_event=buffered_result,
            language=language,
        )
```

Remove the old inline code that:
- Calls `_scan_workspace_for_generated_files`
- Calls `_emit_file_result`
- Calls `_auto_generate_title`
- Buffers `session_state_changed:completed`
- Buffers `buffered_result`
- Calls `buffer.mark_done()`
- Calls `agent_log.end_session()`
- Records obs `session_complete`
- Fires `_summarize_and_store_session`
- Fires `skill_manager.migrate_from_filesystem()`

These are all now handled inside `_finish_task`.

- [ ] **Step 2: Run full test suite**

```
uv run pytest -v
```
Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add main_server.py
git commit -m "refactor: use _finish_task in run_agent_task post-loop

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6: Refactor `run_agent_task_container` to use `message_to_dicts`, `process_event`, `_finish_task`

**Files:**
- Modify: `main_server.py:2650-2897` (`run_agent_task_container`)
- Modify: `src/container_bridge.py:49-415` (`ContainerBridge.__init__`, `run_and_stream`, `_handle_permission_check`)

- [ ] **Step 1: Update `ContainerBridge.__init__` to accept `ctx`, `model`, `tool_use_names`**

In `src/container_bridge.py`, modify `__init__`:

```python
class ContainerBridge:
    """WebSocket client bridging main_server to a container's agent_server."""

    def __init__(
        self,
        container_url: str,
        session_id: str,
        user_id: str,
        buffer,  # MessageBuffer
        session_store=None,
        skill_manager=None,
        ctx=None,  # EventContext — new param
        model=None,  # str | None — new param
        tool_use_names=None,  # dict[str, str] — new param
    ):
        self.container_url = container_url
        self.session_id = session_id
        self.user_id = user_id
        self.buffer = buffer
        self.session_store = session_store
        self.skill_manager = skill_manager

        self.ctx = ctx
        self.model = model
        self.tool_use_names = tool_use_names or {}

        self._ws: ClientConnection | None = None
        self._receive_task: asyncio.Task | None = None
        self._receive_queue: asyncio.Queue = asyncio.Queue()
        self._cancel_event: asyncio.Event = asyncio.Event()
        self._error: str | None = None
        self._result: dict[str, Any] | None = None
```

- [ ] **Step 2: Simplify `run_and_stream` to delegate event processing**

Replace the event-processing switch in `run_and_stream` (lines ~212-332) with the delegated version:

```python
    async def run_and_stream(self, prompt: str, options: dict) -> None:
        """Full lifecycle: connect, run, stream events to shared pipeline."""
        await self.connect()

        self._receive_task = asyncio.create_task(
            self._receive_loop(),
            name=f"bridge-recv-{self.session_id}",
        )

        await self.send_run(prompt, options)

        accumulated_text = ""
        explicit_assistant = False

        try:
            while True:
                if self._cancel_event.is_set():
                    await self.send_cancel()
                    break

                try:
                    data = await asyncio.wait_for(self._receive_queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    if self._receive_task.done():
                        exc = self._receive_task.exception()
                        error_msg = (
                            f"Container receive task died: {exc}" if exc
                            else "Container receive task ended unexpectedly"
                        )
                        logger.error("Receive task failed for session %s: %s", self.session_id, exc)
                        if not self._error:
                            await self.buffer.add_message(self.session_id, {
                                "type": "error",
                                "message": error_msg,
                            }, self.user_id)
                            self._error = error_msg
                        break
                    continue

                # Touch user activity
                try:
                    from src.container_manager import touch_user as _touch  # noqa: PLC0415
                    _touch(self.user_id)
                except ImportError:
                    pass

                msg_type = data.get("type", "")

                # ── Bridge-specific: bidirectional AskUserQuestion ──
                if msg_type == "permission_check":
                    await self._handle_permission_check(data)
                    continue

                # ── Terminal signals ──
                if msg_type == "done":
                    logger.info("Container task done for session %s", self.session_id)
                    if accumulated_text.strip() and not explicit_assistant:
                        logger.info(
                            "Bridge emitting synthetic assistant message len=%d for session %s",
                            len(accumulated_text), self.session_id,
                        )
                        await self.buffer.add_message(self.session_id, {
                            "type": "assistant",
                            "content": accumulated_text,
                        }, self.user_id)
                    break

                if msg_type == "error":
                    raw_msg = data.get("message", "")
                    self._error = raw_msg or "Container agent error (empty message)"
                    logger.error(
                        "Container error for session %s: message=%r",
                        self.session_id, raw_msg,
                    )
                    await self.buffer.add_message(self.session_id, {
                        "type": "error",
                        "message": self._error,
                    }, self.user_id)
                    break

                if msg_type == "cancelled":
                    logger.info("Container task cancelled for session %s", self.session_id)
                    break

                # ── Delegate everything else to shared pipeline ──
                for event in message_to_dicts(
                    data, model=self.model, tool_use_names=self.tool_use_names
                ):
                    if event.get("type") == "result":
                        self._result = event
                        continue

                    # Track streaming text for synthetic assistant fallback
                    if event["type"] == "assistant":
                        explicit_assistant = True
                    elif event["type"] == "stream_event":
                        inner = event.get("event", {})
                        if inner.get("type") == "content_block_delta":
                            delta = inner.get("delta", {})
                            if isinstance(delta, dict) and delta.get("type") == "text_delta":
                                accumulated_text += delta.get("text", "")

                    if self.ctx is not None:
                        await process_event(self.ctx, event)
        finally:
            await self.close()
```

Use lazy imports inside `run_and_stream` to avoid circular import (`main_server` imports
`ContainerBridge` → `container_bridge` cannot import `message_to_dicts` at module level):

```python
# Inside run_and_stream, at point of use:
from main_server import message_to_dicts  # noqa: PLC0415 — lazy, avoids circular import
from src.event_pipeline import process_event  # noqa: PLC0415
```

Remove these now-unused blocks from `run_and_stream`:
- `stream_event` handler (lines 212-240) — replaced by `message_to_dicts` + `process_event`
- `assistant` handler (lines 241-274) — replaced by `message_to_dicts` + `process_event`
- `user` handler (lines 276-293) — replaced by `message_to_dicts` + `process_event`
- `result` handler (lines 298-299) — simplified to `self._result = event`

- [ ] **Step 3: Update `_handle_permission_check` to accept `ctx`**

```python
    async def _handle_permission_check(self, data: dict) -> None:
        """Handle an AskUserQuestion permission check from the container agent.

        1. Buffer the tool_use via process_event (shared pipeline)
        2. Register a Future keyed by session_id
        3. Wait for the browser's answer
        4. Forward the answer to the container
        """
        tool_use_id = data.get("tool_use_id", "")
        tool_input = data.get("tool_input", {})

        # Use shared pipeline for buffer write
        if self.ctx is not None:
            await process_event(self.ctx, {
                "type": "tool_use",
                "name": "AskUserQuestion",
                "id": tool_use_id,
                "input": tool_input,
            })
        else:
            # Fallback: direct buffer write (backward compat)
            await self.buffer.add_message(self.session_id, {
                "type": "tool_use",
                "name": "AskUserQuestion",
                "id": tool_use_id,
                "input": tool_input,
            }, self.user_id)

        # Register future for the browser answer
        answer_future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        bridge_answer_futures[self.session_id] = answer_future

        try:
            answer = await asyncio.wait_for(answer_future, timeout=300)
            await self.send_answer(tool_use_id, answer)
        except asyncio.TimeoutError:
            logger.warning("AskUserQuestion timeout for session %s", self.session_id)
            await self.send_answer(tool_use_id, {"error": "timeout"})
        finally:
            bridge_answer_futures.pop(self.session_id, None)
```

- [ ] **Step 4: Update `run_agent_task_container` to pass ctx and use `_finish_task`**

In `main_server.py`, in `run_agent_task_container`, update the bridge construction and post-loop:

```python
    from src.event_pipeline import EventContext, process_event, _finish_task
    from src.observation import ToolObserver

    # ... build options_dict as before ...

    tool_observer = ToolObserver(_obs_store, session_id, user_id)
    generated_files: list[dict[str, Any]] = []
    tool_use_names: dict[str, str] = {}

    ctx = EventContext(
        user_id=user_id,
        session_id=session_id,
        buffer=buffer,
        observer=tool_observer,
        skill_manager=_skill_manager,
        generated_files=generated_files,
    )

    bridge = ContainerBridge(
        container_url=container_url,
        session_id=session_id,
        user_id=user_id,
        buffer=buffer,
        session_store=session_store,
        skill_manager=_skill_manager,
        ctx=ctx,
        model=options_dict.get("model"),
        tool_use_names=tool_use_names,
    )

    # ... build prompt, run bridge.run_and_stream(prompt, options_dict) ...

    await _finish_task(
        session_id=session_id,
        user_id=user_id,
        buffer=buffer,
        workspace=workspace,
        session_store=session_store,
        skill_manager=_skill_manager,
        obs_store=_obs_store,
        agent_log=agent_log,
        pre_scan_snapshot=pre_scan_snapshot or set(),
        result_event=bridge._result if bridge else None,
        language=language,
    )
```

Replace the entire post-loop section (lines ~2749-2796) that duplicates the teardown logic. Remove the inline calls to `_scan_workspace_for_generated_files`, `_emit_file_result`, `_auto_generate_title`, `buffer.add_message(session_state_changed)`, `buffer.add_message(bridge._result)`, `buffer.mark_done`, `agent_log.end_session`, `obs_store.record`, `_summarize_and_store_session`, `skill_manager.migrate_from_filesystem`.

- [ ] **Step 5: Run full test suite**

```
uv run pytest -v
```

- [ ] **Step 6: Type-check all changed files**

```
uv run mypy main_server.py src/container_bridge.py src/event_pipeline.py
```

- [ ] **Step 7: Commit**

```bash
git add main_server.py src/container_bridge.py
git commit -m "refactor: use shared pipeline in container mode via message_to_dicts + process_event

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7: Integration test — container bridge end-to-end

**Files:**
- Create: `tests/unit/test_container_bridge_pipeline.py`

- [ ] **Step 1: Write integration test for bridge pipeline delegation**

```python
# tests/unit/test_container_bridge_pipeline.py
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestContainerBridgePipeline:
    """Verify the bridge correctly delegates to message_to_dicts + process_event."""

    @pytest.mark.asyncio
    async def test_assistant_dict_flows_through_pipeline(self):
        """Bridge receives assistant WS dict → message_to_dicts → process_event."""
        from src.event_pipeline import EventContext, process_event

        ctx = EventContext(
            user_id="u1", session_id="s1",
            buffer=AsyncMock(), observer=None,
            skill_manager=None, generated_files=[],
        )

        assistant_msg = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I'll help."},
                ],
            },
        }

        from main_server import message_to_dicts

        events = list(message_to_dicts(assistant_msg))
        for event in events:
            await process_event(ctx, event)

        # Verify buffer received the assistant text
        add_calls = [c[0][1] for c in ctx.buffer.add_message.call_args_list]
        assert any(c["type"] == "assistant" and c["content"] == "I'll help." for c in add_calls)

    @pytest.mark.asyncio
    async def test_user_dict_with_tool_result_flows_through_pipeline(self):
        """Bridge receives user WS dict → tool_result emitted → process_event."""
        ctx = EventContext(
            user_id="u1", session_id="s1",
            buffer=AsyncMock(), observer=AsyncMock(),
            skill_manager=None, generated_files=[],
        )

        user_msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu1", "content": "output"},
                ],
            },
        }

        from main_server import message_to_dicts
        from src.event_pipeline import process_event

        events = list(message_to_dicts(user_msg))
        for event in events:
            await process_event(ctx, event)

        add_calls = [c[0][1] for c in ctx.buffer.add_message.call_args_list]
        assert any(c["type"] == "tool_result" for c in add_calls)

    @pytest.mark.asyncio
    async def test_result_dict_stored_in_bridge(self):
        """Result events are captured for deferred emission by _finish_task."""
        result_msg = {
            "type": "result",
            "subtype": "success",
            "duration_ms": 3000,
            "num_turns": 2,
            "is_error": False,
        }

        from main_server import message_to_dicts

        events = list(message_to_dicts(result_msg))
        assert len(events) == 1
        assert events[0]["type"] == "result"
        assert events[0]["duration_ms"] == 3000

    @pytest.mark.asyncio
    async def test_stream_event_dict_preserved_as_is(self):
        """Stream events pass through message_to_dicts unchanged in structure."""
        stream_msg = {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "streaming..."},
            },
        }

        from main_server import message_to_dicts

        events = list(message_to_dicts(stream_msg))
        assert len(events) == 1
        assert events[0]["type"] == "stream_event"
        assert events[0]["event"]["delta"]["text"] == "streaming..."

    @pytest.mark.asyncio
    async def test_tool_use_names_shared_across_messages(self):
        """tool_use_names dict survives across assistant → user message boundaries."""
        from main_server import message_to_dicts

        tool_use_names: dict[str, str] = {}

        # Assistant message defines the tool
        list(message_to_dicts({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu_abc", "name": "Grep", "input": {}},
                ],
            },
        }, tool_use_names=tool_use_names))
        assert tool_use_names["tu_abc"] == "Grep"

        # User message with tool_result resolves the name
        results = list(message_to_dicts({
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_abc", "content": "results..."},
                ],
            },
        }, tool_use_names=tool_use_names))
        assert results[0]["name"] == "Grep"  # resolved, not "unknown"
```

- [ ] **Step 2: Run integration tests**

```
uv run pytest tests/unit/test_container_bridge_pipeline.py -v
```
Expected: All tests PASS.

- [ ] **Step 3: Check full test suite**

```
uv run pytest -v
```

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_container_bridge_pipeline.py
git commit -m "test: add integration tests for container bridge pipeline delegation

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 8: Final verification and cleanup

- [ ] **Step 1: Run full test suite one final time**

```
uv run pytest -v --tb=short
```

- [ ] **Step 2: Run type check across all changed files**

```
uv run mypy main_server.py src/container_bridge.py src/event_pipeline.py
```

- [ ] **Step 3: Run linter**

```
uv run ruff check main_server.py src/container_bridge.py src/event_pipeline.py
```

- [ ] **Step 4: Verify no dead imports in `container_bridge.py`**

The bridge should no longer import `process_content_blocks`, `record_skill_usage_from_event`, `maybe_truncate_tool_result_content` directly — it now delegates all of those to `message_to_dicts` + `process_event`.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore: final cleanup after unified event pipeline refactor

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

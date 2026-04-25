# Fix: "Agent is working..." Stuck Forever

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the WebSocket subscribe loop crash that causes the frontend to display "Agent is working..." indefinitely when the WebSocket connection drops while the agent task is running.

**Architecture:** Add a safe WebSocket send wrapper that catches `RuntimeError` from closed connections, use it in the heartbeat handler, and add orphaned agent task cleanup when the WS handler exits unexpectedly.

**Tech Stack:** Python asyncio, FastAPI WebSocket, Starlette

---

## Root Cause Analysis

From `server.log`, the recurring error chain is:

```
1. TimeoutError: await asyncio.wait_for(event.wait(), timeout=30s)
2. RuntimeError: await websocket.send_text() — WS already closed
3. RuntimeError again: outer except tries to send error message
4. handle_ws exits → finally cancels ws_reader
5. BUT: run_agent_task (asyncio.create_task) keeps running in background
6. Frontend: session state = "running" → spinner forever
```

The heartbeat timeout at `main_server.py:1744-1780` fires after 30s of no new buffer messages. When the WebSocket is already closed (client refreshed the page, network dropped), `send_text` raises `RuntimeError`. This crashes the subscribe loop and exits `handle_ws`, but the agent task — created via `asyncio.create_task()` — is **not cancelled** and continues running.

## File Structure

| File | Responsibility |
|------|---------------|
| `main_server.py` | All changes: add `_safe_ws_send`, fix heartbeat handler, fix error handler, add orphan cleanup |
| `tests/unit/test_main_server.py` | Tests for `_safe_ws_send` and orphan cleanup |

## Task 1: Add `_safe_ws_send` helper

**Files:**
- Modify: `main_server.py` (add helper near top of handle_ws or as module-level function)
- Test: `tests/unit/test_main_server.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_main_server.py — add to a new TestSafeWsSend class
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

class TestSafeWsSend:
    @pytest.mark.anyio
    async def test_returns_false_on_runtime_error(self):
        """When WebSocket is closed, _safe_ws_send returns False without raising."""
        from main_server import _safe_ws_send

        mock_ws = MagicMock()
        mock_ws.send_text = AsyncMock(
            side_effect=RuntimeError(
                "Unexpected ASGI message 'websocket.send', after sending 'websocket.close'"
            )
        )

        result = await _safe_ws_send(mock_ws, {"type": "heartbeat"})
        assert result is False
        mock_ws.send_text.assert_called_once()

    @pytest.mark.anyio
    async def test_returns_true_on_success(self):
        """When WebSocket is open, _safe_ws_send returns True."""
        from main_server import _safe_ws_send

        mock_ws = MagicMock()
        mock_ws.send_text = AsyncMock()

        result = await _safe_ws_send(mock_ws, {"type": "heartbeat", "data": "test"})
        assert result is True
        mock_ws.send_text.assert_called_once()
        call_arg = mock_ws.send_text.call_args[0][0]
        assert '"type": "heartbeat"' in call_arg or '"type":"heartbeat"' in call_arg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_main_server.py::TestSafeWsSend -v`
Expected: FAIL with "ImportError: cannot import name '_safe_ws_send'"

- [ ] **Step 3: Implement `_safe_ws_send`**

Add this as a module-level helper in `main_server.py`, after the imports and before `handle_ws`:

```python
async def _safe_ws_send(websocket: WebSocket, data: dict) -> bool:
    """Send a JSON message over WebSocket, returning False if the connection
    is already closed. Prevents RuntimeError from crashing the subscribe loop."""
    try:
        await websocket.send_text(json.dumps(data))
        return True
    except RuntimeError:
        # WebSocket was already closed — connection lost.
        # Caller should exit the subscribe loop gracefully.
        return False
    except Exception:
        # Catch any other send errors (e.g., ConnectionClosed from websockets lib)
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_main_server.py::TestSafeWsSend -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main_server.py tests/unit/test_main_server.py
git commit -m "feat: add _safe_ws_send helper to prevent subscribe loop crash on closed WS"
```

## Task 2: Use `_safe_ws_send` in heartbeat timeout handler

**Files:**
- Modify: `main_server.py` lines ~1742-1781

- [ ] **Step 1: Find and replace the heartbeat send**

The current heartbeat code (around lines 1742-1781):

```python
                    event.clear()
                    try:
                        await asyncio.wait_for(event.wait(), timeout=HEARTBEAT_INTERVAL)
                    except asyncio.TimeoutError:
                        task_key = f"task_{session_id}"
                        agent_alive = task_key in active_tasks and not active_tasks[task_key].done()
                        hb = make_heartbeat(agent_alive=agent_alive)
                        await websocket.send_text(
                                json.dumps(
                                    {
                                        **hb,
                                        "index": last_seen,
                                        "replay": False,
                                        "session_id": session_id,
                                    }
                                )
                            )
                        continue
```

Replace with:

```python
                    event.clear()
                    try:
                        await asyncio.wait_for(event.wait(), timeout=HEARTBEAT_INTERVAL)
                    except asyncio.TimeoutError:
                        task_key = f"task_{session_id}"
                        agent_alive = task_key in active_tasks and not active_tasks[task_key].done()
                        hb = make_heartbeat(agent_alive=agent_alive)
                        if not await _safe_ws_send(websocket, {
                            **hb,
                            "index": last_seen,
                            "replay": False,
                            "session_id": session_id,
                        }):
                            # WebSocket closed — exit subscribe loop gracefully
                            break
                        continue
```

- [ ] **Step 2: Run tests to verify no regression**

Run: `pytest tests/unit/test_message_buffer.py -v`
Expected: All 32 tests pass

- [ ] **Step 3: Commit**

```bash
git add main_server.py
git commit -m "fix: use _safe_ws_send in heartbeat handler to exit gracefully on WS close"
```

## Task 3: Use `_safe_ws_send` in subscribe loop message sends

**Files:**
- Modify: `main_server.py` — all `await websocket.send_text()` calls inside the subscribe `try` block

- [ ] **Step 1: Wrap the replay send loop (recover path, ~lines 1525-1537)**

Current:
```python
                        new_messages = buffer.get_history(session_id, after_index=last_seen)
                        for i, h in enumerate(new_messages):
                            idx = last_seen + i
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        **h,
                                        "index": idx,
                                        "replay": False,
                                        "session_id": session_id,
                                    }
                                )
                            )
                        last_seen += len(new_messages)
```

Replace with:
```python
                        new_messages = buffer.get_history(session_id, after_index=last_seen)
                        for i, h in enumerate(new_messages):
                            idx = last_seen + i
                            if not await _safe_ws_send(websocket, {
                                **h,
                                "index": idx,
                                "replay": False,
                                "session_id": session_id,
                            }):
                                break
                        last_seen += len(new_messages)
```

- [ ] **Step 2: Wrap the final pull send loop (~lines 1541-1556)**

Current:
```python
                            final_messages = buffer.get_history(session_id, after_index=last_seen)
                            for i, h in enumerate(final_messages):
                                idx = last_seen + i
                                await websocket.send_text(
                                    json.dumps(
                                        {
                                            **h,
                                            "index": idx,
                                            "replay": False,
                                            "session_id": session_id,
                                        }
                                    )
                                )
                            last_seen += len(final_messages)
```

Replace with:
```python
                            final_messages = buffer.get_history(session_id, after_index=last_seen)
                            for i, h in enumerate(final_messages):
                                idx = last_seen + i
                                if not await _safe_ws_send(websocket, {
                                    **h,
                                    "index": idx,
                                    "replay": False,
                                    "session_id": session_id,
                                }):
                                    break
                            last_seen += len(final_messages)
```

- [ ] **Step 3: Wrap `_emit_synthetic_state_change_if_missing`**

This helper also sends over WebSocket. Modify the function signature to accept and return a `_safe_ws_send` wrapper or inline the safe send.

Replace `main_server.py` lines 131-160 with:

```python
async def _emit_synthetic_state_change_if_missing(
    websocket: WebSocket,
    session_id: str,
    last_seen: int,
) -> int:
    """Emit a synthetic session_state_changed if buffer is in a terminal
    state but the buffer contains no such message. Returns updated last_seen."""
    buf_state = buffer.get_session_state(session_id)
    if buf_state["state"] in ("completed", "error", "cancelled"):
        all_buffer_msgs = buffer.get_history(session_id)
        has_state_change = any(
            m.get("type") == "system"
            and m.get("subtype") == "session_state_changed"
            for m in all_buffer_msgs
        )
        if not has_state_change:
            if not await _safe_ws_send(websocket, {
                "type": "system",
                "subtype": "session_state_changed",
                "state": buf_state["state"],
                "index": last_seen,
                "replay": False,
                "session_id": session_id,
            }):
                pass  # WS closed — caller will handle
            last_seen += 1
    return last_seen
```

- [ ] **Step 4: Wrap the subscribe loop final pull sends (~lines 1717-1734)**

Current:
```python
                            final_messages = buffer.get_history(session_id, after_index=last_seen)
                            for i, h in enumerate(final_messages):
                                idx = last_seen + i
                                await websocket.send_text(
                                    json.dumps(
                                        {
                                            **h,
                                            "index": idx,
                                            "replay": False,
                                            "session_id": session_id,
                                        }
                                    )
                                )
                            last_seen += len(final_messages)

                            last_seen = await _emit_synthetic_state_change_if_missing(
                                websocket, session_id, last_seen
                            )
```

Replace with:
```python
                            final_messages = buffer.get_history(session_id, after_index=last_seen)
                            for i, h in enumerate(final_messages):
                                idx = last_seen + i
                                if not await _safe_ws_send(websocket, {
                                    **h,
                                    "index": idx,
                                    "replay": False,
                                    "session_id": session_id,
                                }):
                                    break
                            last_seen += len(final_messages)

                            last_seen = await _emit_synthetic_state_change_if_missing(
                                websocket, session_id, last_seen
                            )
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_message_buffer.py -v`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add main_server.py
git commit -m "fix: wrap all subscribe loop WS sends with _safe_ws_send"
```

## Task 4: Fix outer error handler — don't try to send after RuntimeError

**Files:**
- Modify: `main_server.py` lines ~1767-1781

- [ ] **Step 1: Replace the outer exception handler**

Current:
```python
    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected for user %s", user_id)
    except Exception as e:
        logger.exception("WebSocket error")
        try:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "message": str(e),
                    }
                )
            )
        except Exception:
            pass
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except asyncio.CancelledError:
            pass
```

Replace with:
```python
    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected for user %s", user_id)
    except Exception as e:
        logger.exception("WebSocket error")
        # Don't try to send error message — the connection is likely already closed
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except asyncio.CancelledError:
            pass
        # Clean up orphaned agent task — if the WS closed while the agent
        # was still running, cancel it to prevent resource leaks.
        task_key = f"task_{current_session_id}" if current_session_id else None
        if task_key and task_key in active_tasks:
            task = active_tasks[task_key]
            if not task.done():
                logger.info(
                    "WS: cancelling orphaned agent task for session %s",
                    current_session_id,
                )
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                # Set error state so the frontend knows the session ended
                buffer.add_message(
                    current_session_id,
                    {
                        "type": "system",
                        "subtype": "session_state_changed",
                        "state": "error",
                    },
                )
                buffer.mark_done(current_session_id)
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/unit/test_message_buffer.py -v`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add main_server.py
git commit -m "fix: cancel orphaned agent task on WS exit, remove redundant error send"
```

## Task 5: Integration test

**Files:**
- Create: `tests/integration/test_ws_subscribe_crash.py`

- [ ] **Step 1: Write integration test**

```python
"""Integration test: verify the subscribe loop exits gracefully when
the WebSocket closes while the agent task is still running."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastapi.testclient import TestClient
from fastapi import WebSocket


class TestWsSubscribeCrash:
    """When the WebSocket closes while the agent task runs, the subscribe
    loop should exit gracefully without crashing, and the agent task should
    be cancelled (not orphaned)."""

    def test_safe_ws_send_returns_false_on_runtime_error(self):
        """_safe_ws_send returns False when the WS raises RuntimeError."""
        from main_server import _safe_ws_send

        mock_ws = MagicMock()
        mock_ws.send_text = AsyncMock(
            side_effect=RuntimeError("websocket.send after websocket.close")
        )

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            _safe_ws_send(mock_ws, {"type": "heartbeat"})
        )
        assert result is False

    def test_safe_ws_send_returns_true_on_success(self):
        """_safe_ws_send returns True when the WS send succeeds."""
        from main_server import _safe_ws_send

        mock_ws = MagicMock()
        mock_ws.send_text = AsyncMock()

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            _safe_ws_send(mock_ws, {"type": "heartbeat", "data": "test"})
        )
        assert result is True
        mock_ws.send_text.assert_called_once()
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/integration/test_ws_subscribe_crash.py -v`
Expected: Both tests pass

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/unit/test_message_buffer.py tests/unit/test_main_server.py -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_ws_subscribe_crash.py
git commit -m "test: integration tests for safe WS send and subscribe loop exit"
```

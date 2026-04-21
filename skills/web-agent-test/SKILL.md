---
name: web-agent-test
description: Testing patterns for the Web Agent project — FastAPI backend, React frontend, JWT auth, WebSocket streaming, and per-user session isolation.
---

# Web Agent Testing Skill

Test patterns extracted from the Web Agent codebase. Covers REST endpoints, WebSocket streaming, JWT auth flows, session lifecycle, and file management.

## Quick Start

```bash
# Backend tests (all)
uv run pytest tests/unit/ -v

# Single test class
uv run pytest tests/unit/test_main_server.py::TestSessionStatus -v

# With coverage
uv run pytest --cov=src --cov-report=term-missing tests/unit/

# Frontend tests
cd frontend && npm test
```

## Test Architecture

### SDK Mocking Strategy

`main_server.py` imports `claude_agent_sdk` at module level. You **must** mock the entire SDK package **before** importing the server module:

```python
import sys
from unittest.mock import MagicMock

_mock_sdk = MagicMock()
_mock_sdk.ClaudeSDKClient = MagicMock()
_mock_sdk.types = MagicMock()
_mock_sdk.types.AssistantMessage = MagicMock
_mock_sdk.types.TextBlock = MagicMock
# ... add any types the server references
sys.modules["claude_agent_sdk"] = _mock_sdk
sys.modules["claude_agent_sdk.types"] = _mock_sdk.types

# Now safe to import
from fastapi.testclient import TestClient
import main_server
```

### Data Directory Isolation

Each test gets a fresh temporary directory. Use the `tmp_path` fixture to redirect `DATA_ROOT`:

```python
import pytest
from pathlib import Path
import main_server


@pytest.fixture(autouse=True)
def _patch_data_root(tmp_path: Path) -> None:
    """Redirect DATA_ROOT to a temporary directory for each test."""
    main_server.DATA_ROOT = tmp_path
    main_server.buffer = main_server.MessageBuffer(base_dir=tmp_path / ".msg-buffer")
    main_server.active_tasks.clear()
    main_server.pending_answers.clear()
    (tmp_path / "users").mkdir(exist_ok=True)
    for user in ("alice", "bob", "default"):
        (tmp_path / "users" / user).mkdir(exist_ok=True)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(main_server.app)
```

## Testing Patterns by Surface Area

### REST Endpoints

Use FastAPI `TestClient` with path-scoped URLs (`/api/users/{user_id}/...`):

```python
class TestCreateSession:
    def test_create_session_returns_id(self, client: TestClient) -> None:
        resp = client.post("/api/users/alice/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["session_id"].startswith("session_alice_")
```

### JWT Authentication

Test both `ENFORCE_AUTH=False` (dev default) and `ENFORCE_AUTH=True` paths:

```python
from unittest.mock import patch
from src.auth import create_token, get_current_user, require_user_match


class TestAuth:
    def test_returns_default_when_auth_disabled(self) -> None:
        user_id = get_current_user(token=None)
        assert user_id == "default"

    def test_raises_when_no_token_and_auth_enabled(self) -> None:
        with patch("src.auth.ENFORCE_AUTH", True):
            with pytest.raises(HTTPException) as exc_info:
                get_current_user(token=None)
            assert exc_info.value.status_code == 401

    def test_cross_user_isolation(self) -> None:
        with patch("src.auth.ENFORCE_AUTH", True):
            with pytest.raises(HTTPException) as exc_info:
                require_user_match(path_user_id="bob", current_user="alice")
            assert exc_info.value.status_code == 403
```

### Auth Header Extraction (Regression Pattern)

When testing endpoints that accept optional auth, verify the `Authorization` header is actually extracted (not silently dropped):

```python
class TestAuthHeaderExtraction:
    def test_header_is_extracted(self, client: TestClient) -> None:
        with patch("main_server._get_user_id_from_header", return_value="alice") as mock_fn:
            resp = client.post(
                "/api/skills/test-skill/feedback",
                headers={"Authorization": "Bearer some-token"},
                json={"rating": 5, "comment": "With auth"},
            )
            assert resp.status_code == 200
            mock_fn.assert_called_once()
            call_arg = mock_fn.call_args[1].get("authorization")
            assert call_arg == "Bearer some-token"

    def test_header_none_when_not_sent(self, client: TestClient) -> None:
        with patch("main_server._get_user_id_from_header", return_value="default") as mock_fn:
            resp = client.post(
                "/api/skills/test-skill/feedback",
                json={"rating": 3, "comment": "No auth"},
            )
            assert resp.status_code == 200
            mock_fn.assert_called_once()
            assert mock_fn.call_args[1].get("authorization") is None
```

### WebSocket Streaming

Test connection lifecycle, message replay, and recovery:

```python
import json

class TestWebSocket:
    def test_connect_and_disconnect(self, client: TestClient) -> None:
        with client.websocket_connect("/ws"):
            pass  # connect, then exit — no exception expected

    def test_recover_replays_messages(self, client: TestClient) -> None:
        buf = main_server.buffer
        sid = "session_recover_test"
        buf.add_message(sid, {"type": "user", "content": "hello"})
        buf.add_message(sid, {"type": "assistant", "content": "hi back"})
        buf.mark_done(sid)

        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({
                "type": "recover",
                "session_id": sid,
                "user_id": "alice",
                "last_index": 0,
            }))
            msg1 = json.loads(ws.receive_text())
            assert msg1["type"] == "user"
            assert msg1["replay"] is True
            assert msg1["index"] == 0

    def test_recover_partial_from_index(self, client: TestClient) -> None:
        """Only messages after last_index are replayed."""
        buf = main_server.buffer
        sid = "session_partial"
        for i in range(4):
            buf.add_message(sid, {"type": "user", "content": f"msg-{i}"})
        buf.mark_done(sid)

        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({
                "type": "recover",
                "session_id": sid,
                "user_id": "alice",
                "last_index": 2,
            }))
            msg = json.loads(ws.receive_text())
            assert msg["content"] == "msg-2"
            assert msg["index"] == 2
```

### Session Lifecycle & Message Buffer

Test state transitions and message ordering — critical for the WebSocket subscribe loop:

```python
class TestSessionLifecycle:
    def test_status_of_new_session(self, client: TestClient) -> None:
        resp = client.post("/api/users/alice/sessions")
        sid = resp.json()["session_id"]
        resp = client.get(f"/api/users/alice/sessions/{sid}/status")
        assert resp.json()["state"] == "idle"
        assert resp.json()["cost_usd"] == 0.0

    def test_cancel_marks_session_done(self, client: TestClient) -> None:
        resp = client.post("/api/users/alice/sessions")
        sid = resp.json()["session_id"]
        resp = client.post(f"/api/users/alice/sessions/{sid}/cancel")
        assert resp.status_code == 200
        state = main_server.buffer.get_session_state(sid)
        assert state["state"] == "cancelled"

    def test_result_message_marks_buffer_done(self) -> None:
        """Result messages must transition state to completed so the WS loop exits."""
        sid = "test_result_done"
        main_server.buffer.add_message(sid, {"type": "system", "subtype": "progress"})
        assert main_server.buffer.is_done(sid) is False

        main_server.buffer.add_message(sid, {
            "type": "result", "subtype": "success", "session_id": sid,
        })
        assert main_server.buffer.is_done(sid) is True
        assert main_server.buffer.get_session_state(sid)["state"] == "completed"
```

### Message Ordering (Subscribe Loop Safety)

The subscribe loop does `is_done()` → final `get_history()`. State messages must be in the buffer **before** `mark_done()`:

```python
class TestMessageOrdering:
    def test_state_message_before_mark_done(self) -> None:
        """session_state_changed must be added BEFORE mark_done so the
        subscribe loop's final pull catches it."""
        sid = "test_ordering"
        buf = main_server.buffer

        buf.add_message(sid, {"type": "system", "subtype": "session_state_changed", "state": "completed"})
        buf.mark_done(sid)

        history = buf.get_history(sid)
        assert any(m.get("subtype") == "session_state_changed" for m in history)
        assert buf.is_done(sid) is True

    def test_error_path_ordering(self) -> None:
        """Error messages + state change must both be visible after mark_done."""
        sid = "test_error_order"
        buf = main_server.buffer

        buf.add_message(sid, {"type": "error", "message": "Something failed"})
        buf.add_message(sid, {"type": "system", "subtype": "session_state_changed", "state": "error"})
        buf.mark_done(sid)

        history = buf.get_history(sid)
        assert any(m["type"] == "error" for m in history)
        assert any(m.get("state") == "error" for m in history)
```

### File Management

Test upload, download, path traversal protection, and generated files isolation:

```python
class TestFiles:
    def test_upload_and_list(self, client: TestClient) -> None:
        workspace = main_server.user_data_dir("alice") / "workspace" / "uploads"
        workspace.mkdir(parents=True, exist_ok=True)

        resp = client.post(
            "/api/users/alice/upload",
            files={"file": ("test.txt", b"hello world")},
        )
        assert resp.status_code == 200
        assert resp.json()["size"] == 11

        resp = client.get("/api/users/alice/files")
        assert any(f["path"] == "uploads/test.txt" for f in resp.json())

    def test_path_traversal_blocked(self, client: TestClient) -> None:
        resp = client.get("/api/users/alice/download/../../../etc/passwd")
        assert resp.status_code in (403, 404)

    def test_generated_files_excludes_uploads(self, client: TestClient) -> None:
        """Uploaded files must NOT appear in the generated-files list."""
        uploads_dir = main_server.user_data_dir("alice") / "workspace" / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        (uploads_dir / "data.csv").write_text("col1,col2")

        outputs_dir = main_server.user_data_dir("alice") / "workspace" / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        (outputs_dir / "report.pdf").write_bytes(b"%PDF-fake")

        resp = client.get("/api/users/alice/generated-files")
        filenames = [f["filename"] for f in resp.json()]
        assert "report.pdf" in filenames
        assert "data.csv" not in filenames
```

### Consumer Wake Events

Test that `add_message` wakes waiting consumers via `event.set()`:

```python
def test_state_message_wakes_consumers(self) -> None:
    sid = "test_wake"
    buf = main_server.buffer
    event = buf.subscribe(sid)

    buf.add_message(sid, {"type": "system", "subtype": "session_state_changed", "state": "completed"})
    assert event.is_set() is True
```

### Source Code Ordering Guards

When message ordering bugs are hard to reproduce at runtime, assert on source code structure:

```python
def test_state_before_mark_done_in_source(self) -> None:
    """Verify session_state_changed is called before mark_done in run_agent_task."""
    import inspect
    source = inspect.getsource(main_server.run_agent_task)
    lines = source.split('\n')

    state_msg_line = None
    mark_done_line = None
    for i, line in enumerate(lines):
        if '"session_state_changed"' in line and state_msg_line is None:
            state_msg_line = i
        if 'buffer.mark_done' in line and mark_done_line is None:
            mark_done_line = i

    assert state_msg_line is not None
    assert state_msg_line < mark_done_line, (
        f"BUG: mark_done at line {mark_done_line} before state at line {state_msg_line}"
    )
```

## Test Organization

| File | Coverage |
|------|----------|
| `test_main_server.py` | REST endpoints, WebSocket, session lifecycle, file management, skills, MCP |
| `test_auth.py` | JWT token creation, verification, expiry, user matching |
| `test_message_buffer.py` | Buffer operations, consumer events, history retrieval |
| `test_skill_feedback.py` | Skill feedback submission and ratings |
| `test_skill_feedback_db.py` | DB-backed skill feedback with user edits |
| `test_skill_evolution.py` | Skill evolution from user feedback |
| `test_semiauto_evolution.py` | Semi-automatic skill evolution |
| `test_session_fork.py` | Session forking and branching |
| `test_ab_testing.py` | A/B testing for agent configurations |
| `test_pre_tool_use_hooks.py` | PreToolUse hook execution |
| `test_file_attachments.py` | File attachment handling |
| `test_heartbeat.py` | Heartbeat mechanism (must not inflate cursors) |

## Common Pitfalls

1. **Missing SDK mock types** — If a test crashes on import with `AttributeError` on `claude_agent_sdk.types.X`, add the missing mock type to the SDK mock block at the top of `test_main_server.py`.

2. **Data directory leaks between tests** — Always use the `_patch_data_root` autouse fixture. Without it, tests share the real `data/` directory and pollute each other.

3. **Auth patching scope** — `ENFORCE_AUTH` is a module-level constant. Use `patch("src.auth.ENFORCE_AUTH", True)` or `patch("main_server.ENFORCE_AUTH", True)` depending on which module you're testing.

4. **Buffer state cleanup** — The autouse fixture calls `active_tasks.clear()` and `pending_answers.clear()`. If you add new global state dicts, add their cleanup here too.

5. **Heartbeat cursor inflation** — The subscribe loop sends heartbeats during idle. Heartbeats must NOT increment `last_seen` — they're synthetic messages not stored in the buffer.

6. **Recover check ordering** — In the subscribe loop, the `session_id` check must come BEFORE the `type == "recover"` check. Otherwise recover messages for other sessions hit `continue` and never trigger session switching.

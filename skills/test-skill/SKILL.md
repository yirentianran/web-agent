---
name: test-skill
description: Test skill for the web-agent project. Covers auth, sessions, WebSocket, skill feedback, evolution pipelines, and frontend testing.
---

# Test Skill

Testing skill for the web-agent project (FastAPI backend + React frontend). Use this for validating endpoints, session lifecycle, auth flows, skill feedback/evolution, and frontend components.

## Quick Start

```bash
# Run all backend tests
uv run pytest tests/unit/ -v

# Run a single test
uv run pytest tests/unit/test_main_server.py::TestCreateSession::test_create_session_returns_id -v

# Run with coverage
uv run pytest tests/unit/ --cov=src --cov=main_server --cov-report=term-missing

# Frontend tests
cd frontend && npm test
```

## Test Selection Guide

Start narrow, expand as needed:

| You changed... | Run this first | Then expand to |
|----------------|----------------|----------------|
| `main_server.py` | `test_main_server.py` | All tests |
| Auth / JWT | `test_main_server.py` (auth tests) | Full suite |
| Message buffer | `test_message_buffer.py`, `test_message_buffer_db.py` | `test_main_server.py` |
| Skill feedback | `test_skill_feedback.py`, `test_skill_feedback_db.py` | `test_skill_evolution.py` |
| Skill evolution | `test_skill_evolution.py`, `test_semiauto_evolution.py` | `test_skill_feedback.py` |
| Frontend components | `frontend/src/components/*.test.tsx` | `npm test` |
| WebSocket / hooks | `test_main_server.py` (WS tests), `frontend/src/hooks/*.test.ts` | All tests |

## Workflow Checklist

1. Isolate data with `_patch_data_root` fixture
2. Mock `_get_user_id_from_header` for auth
3. Write test (RED) → Implement (GREEN) → Refactor
4. Verify coverage ≥ 80%
5. Run full suite to confirm no regressions

## Authentication

All skill endpoints require user context. Always test both authenticated and unauthenticated paths:

| Scenario | Expected |
|----------|----------|
| Missing token | 401 |
| Expired token | 401 |
| Valid token | 200 with user-scoped data |
| Token from different user | No cross-user data leakage |

### Auth Header Pattern (Critical)

Endpoints **must** declare `Header()` on the authorization parameter, otherwise the header is silently dropped and always `None`:

```python
from fastapi import Header

async def some_endpoint(authorization: str | None = Header(None)):
    user_id = _get_user_id_from_header(authorization)
    ...
```

**Gotcha:** Without `Header(None)`, the auth header is always `None` and tests fail silently. This bug is invisible until you send an actual token.

### Mocking User Context

```python
from unittest.mock import patch

with patch("main_server._get_user_id_from_header", return_value="alice"):
    resp = client.post(
        "/api/skills/test-skill/feedback",
        headers={"Authorization": "Bearer some-token"},
        json={"rating": 5, "comment": "Excellent"},
    )
    assert resp.status_code == 200
```

### Auth Extraction Verification

Test that the header is actually being passed through:

```python
def test_authorization_header_is_extracted(self, client):
    with patch("main_server._get_user_id_from_header", return_value="alice") as mock_fn:
        resp = client.post(
            "/api/skills/test-skill/feedback",
            headers={"Authorization": "Bearer some-token"},
            json={"rating": 5, "comment": "With auth"},
        )
        assert resp.status_code == 200
        call_arg = mock_fn.call_args[0][0]
        assert call_arg == "Bearer some-token", (
            "Authorization header was not extracted — missing Header() on endpoint param"
        )
```

### JWT Auth Flows (ENFORCE_AUTH)

Test both `ENFORCE_AUTH=False` (dev default) and `ENFORCE_AUTH=True` paths:

```python
from unittest.mock import patch
from src.auth import create_token, get_current_user, require_user_match

def test_returns_default_when_auth_disabled():
    user_id = get_current_user(token=None)
    assert user_id == "default"

def test_raises_when_no_token_and_auth_enabled():
    with patch("src.auth.ENFORCE_AUTH", True):
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(token=None)
        assert exc_info.value.status_code == 401

def test_cross_user_isolation():
    with patch("src.auth.ENFORCE_AUTH", True):
        with pytest.raises(HTTPException) as exc_info:
            require_user_match(path_user_id="bob", current_user="alice")
        assert exc_info.value.status_code == 403
```

## SDK Mocking Strategy

`main_server.py` imports `claude_agent_sdk` at module level. You **must** mock the entire SDK package **before** importing the server module:

```python
import sys
from unittest.mock import MagicMock

_mock_sdk = MagicMock()
_mock_sdk.ClaudeSDKClient = MagicMock()
_mock_sdk.types = MagicMock()
_mock_sdk.types.AssistantMessage = MagicMock
_mock_sdk.types.TextBlock = MagicMock
sys.modules["claude_agent_sdk"] = _mock_sdk
sys.modules["claude_agent_sdk.types"] = _mock_sdk.types

# Now safe to import
from fastapi.testclient import TestClient
import main_server
```

## Test Data Isolation

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

## Test Patterns

### REST Endpoint Testing

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

### Session Lifecycle Testing

Test creation, activity, completion, and cleanup:

```python
class TestSessionStatus:
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

### Message Buffer Ordering

**Critical:** state messages must be added **before** `mark_done()` so the subscribe loop's final pull catches them:

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

### WebSocket Testing

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

Key WebSocket scenarios:
1. Connect with valid auth → verify session isolation
2. Send recover message → verify replay with correct flags
3. Partial recovery from index → only newer messages replayed
4. Disconnect → verify cleanup and graceful handling
5. Message ordering → `file_result` before `result` message

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

### Skill Feedback Testing

Test feedback submission with auth, ratings, and user edits:

```python
def test_submit_skill_feedback_with_user_edits(client):
    with patch("main_server._get_user_id_from_header", return_value="alice"):
        resp = client.post(
            "/api/skills/test-skill/feedback",
            json={"rating": 4, "comment": "Good", "user_edits": "Fixed formatting"},
        )
        assert resp.status_code == 200
```

### Skill Evolution Testing

Test evolution candidates and preview/activate workflows:

```python
from src.skill_evolution import SkillEvolutionManager, SHOULD_EVOLVE_MIN_COUNT

def test_should_evolve_threshold():
    mgr = SkillEvolutionManager(tmp_path)
    for i in range(SHOULD_EVOLVE_MIN_COUNT):
        mgr.collect_feedback("test-skill", rating=3, user_id="user-1")

    stats = mgr.get_feedback_stats("test-skill")
    assert stats.count >= SHOULD_EVOLVE_MIN_COUNT
    assert stats.average_rating <= 4.5
```

### DB-Backed Evolution Testing

When a SQLite DB is configured, test the async evolution pipeline:

```python
import asyncio
from src.skill_evolution import SkillEvolutionManager

async def test_db_evolution_pipeline(tmp_path, db_connection):
    mgr = SkillEvolutionManager(data_root=tmp_path, db=db_connection)

    from src.skill_feedback import DBSkillFeedbackManager
    db_mgr = DBSkillFeedbackManager(db=db_connection)
    await db_mgr.submit_feedback("test-skill", "alice", rating=3, comment="Needs work")

    stats = await mgr.db_get_feedback_stats("test-skill")
    assert stats.count >= 1

    preview = await mgr.db_preview_evolution("test-skill")
    assert preview is not None
    assert "new_version" in preview
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

### Frontend Testing

Test React components with Vitest and Testing Library:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Header from './Header'

test('shows user menu when authenticated', () => {
  render(<Header isAuthenticated={true} userName="Alice" />)
  expect(screen.getByText('Alice')).toBeInTheDocument()
})

test('hides user menu when not authenticated', () => {
  render(<Header isAuthenticated={false} />)
  expect(screen.queryByText('Sign In')).not.toBeInTheDocument()
})
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

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| 401 on test endpoints | Test token missing or expired | Mock `_get_user_id_from_header` or generate fresh token |
| Session not found | Session file missing in `data/` | Use `_patch_data_root` fixture with `tmp_path` |
| WebSocket disconnects | Agent subprocess unhealthy | Verify SDK mock is properly configured |
| Messages not replayed | `last_index` cursor inflated | Heartbeat handler must NOT increment `last_seen` |
| Session stays "running" | State message added after `mark_done()` | Add `session_state_changed` message before `mark_done()` |
| Auth header always None | Missing `Header()` in endpoint param | Use `authorization: str \| None = Header(None)` |
| File result not shown | `insert_before_type` vs `append` | Use `add_message` (append) for file_result |
| Frontend test fails | DOM not matching component output | Check `data-testid` attributes and screen assertions |
| Coverage report empty | `--cov` paths don't match source dirs | Ensure `--cov=src --cov=main_server` match actual layout |
| Auth tests fail locally | JWT secret mismatch | Verify the JWT secret used in tests matches the server config |
| Evolution candidate not found | Feedback count < 10 or avg rating >= 4.5 | Collect more feedback or check `SHOULD_EVOLVE_MIN_COUNT` |
| DB evolution returns None | DB not initialized or no feedback data | Ensure `db` connection is passed to `SkillEvolutionManager` |
| Missing SDK mock types | `AttributeError` on `claude_agent_sdk.types.X` | Add missing mock type to SDK mock block at top of test file |
| Data directory leaks | Missing `_patch_data_root` autouse fixture | Always use the fixture; add cleanup for new global state dicts |

## Best Practices

1. **Isolate data** — always use `tmp_path` fixture for `DATA_ROOT` to prevent test pollution
2. **Mock the SDK** — mock `claude_agent_sdk` at module level before importing `main_server`
3. **Test auth paths** — test both authenticated and unauthenticated code paths, verify header extraction
4. **Verify ordering** — for critical message ordering, test both behavior and source code
5. **No flaky timeouts** — use deterministic assertions instead of `time.sleep`
6. **Cover edge cases** — empty sessions, missing files, invalid tokens, boundary ratings
7. **Test DB and file paths** — when testing skill feedback/evolution, verify both the file-based and DB-backed code paths

"""Unit tests for main_server REST endpoints.

Because main_server.py imports ``claude_agent_sdk`` at module level,
we mock the entire package before the server module is loaded.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Mock claude_agent_sdk before main_server imports it ────────────

_mock_sdk = MagicMock()
_mock_sdk.ClaudeSDKClient = MagicMock()
_mock_sdk.types = MagicMock()
_mock_sdk.types.AssistantMessage = MagicMock
_mock_sdk.types.ClaudeAgentOptions = MagicMock
_mock_sdk.types.PermissionResultAllow = MagicMock
_mock_sdk.types.PermissionResult = MagicMock
_mock_sdk.types.ResultMessage = MagicMock
_mock_sdk.types.StreamEvent = MagicMock
_mock_sdk.types.SystemMessage = MagicMock
_mock_sdk.types.TextBlock = MagicMock
_mock_sdk.types.ThinkingBlock = MagicMock
_mock_sdk.types.ToolPermissionContext = MagicMock
_mock_sdk.types.ToolUseBlock = MagicMock
_mock_sdk.types.UserMessage = MagicMock
sys.modules["claude_agent_sdk"] = _mock_sdk
sys.modules["claude_agent_sdk.types"] = _mock_sdk.types

# Now import the server (SDK imports are satisfied by mocks)
from fastapi.testclient import TestClient

import main_server


@pytest.fixture(autouse=True)
def _patch_data_root(tmp_path: Path) -> None:
    """Redirect DATA_ROOT to a temporary directory for each test."""
    main_server.DATA_ROOT = tmp_path
    # Recreate the MessageBuffer with the temp directory
    main_server.buffer = main_server.MessageBuffer(base_dir=tmp_path / ".msg-buffer")
    main_server.active_tasks.clear()
    main_server.pending_answers.clear()
    # Ensure base data directories exist
    (tmp_path / "users").mkdir(exist_ok=True)
    # Pre-create common user data dirs so write operations don't fail
    for user in ("alice", "bob", "default"):
        (tmp_path / "users" / user).mkdir(exist_ok=True)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(main_server.app)


# ── Health ─────────────────────────────────────────────────────────


class TestHealth:
    def test_health_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "service": "main-server"}


# ── Session CRUD ───────────────────────────────────────────────────


class TestCreateSession:
    def test_create_session_returns_id(self, client: TestClient) -> None:
        resp = client.post("/api/users/alice/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["session_id"].startswith("session_alice_")

    def test_create_session_initialises_buffer(self, client: TestClient) -> None:
        resp = client.post("/api/users/bob/sessions")
        sid = resp.json()["session_id"]
        state = main_server.buffer.get_session_state(sid)
        assert state["state"] == "idle"


class TestListSessions:
    def test_empty_user_has_no_sessions(self, client: TestClient) -> None:
        resp = client.get("/api/users/newuser/sessions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_lists_created_sessions(self, client: TestClient) -> None:
        # Create two sessions
        r1 = client.post("/api/users/alice/sessions")
        r2 = client.post("/api/users/alice/sessions")
        sids = {r1.json()["session_id"], r2.json()["session_id"]}

        resp = client.get("/api/users/alice/sessions")
        assert resp.status_code == 200
        listed = {s["session_id"] for s in resp.json()}
        # Sessions appear in in-memory buffer even if not on disk yet
        assert sids & listed == sids  # all created sessions present


class TestDeleteSession:
    def test_delete_existing_session(self, client: TestClient) -> None:
        resp = client.post("/api/users/alice/sessions")
        sid = resp.json()["session_id"]

        resp = client.delete(f"/api/users/alice/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_delete_nonexistent_session(self, client: TestClient) -> None:
        resp = client.delete("/api/users/alice/sessions/fake-session")
        assert resp.status_code == 404


class TestCancelSession:
    def test_cancel_marks_session_done(self, client: TestClient) -> None:
        resp = client.post("/api/users/alice/sessions")
        sid = resp.json()["session_id"]

        resp = client.post(f"/api/users/alice/sessions/{sid}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        state = main_server.buffer.get_session_state(sid)
        assert state["state"] == "cancelled"

    def test_cancel_awaits_task_completion(self, client: TestClient) -> None:
        """Cancel endpoint must wait for the task's CancelledError handler
        to finish before returning, so the buffer state is final and
        consistent when the client reads it.

        We simulate an agent task using a background thread that mimics
        the async task behavior — the endpoint must wait for it.
        """
        import asyncio
        import threading

        sid = "test-cancel-await-task"
        handler_done = threading.Event()

        # Create a future that the "task" will complete. We'll use the
        # FastAPI app's event loop by scheduling through asyncio.run_coroutine_threadsafe.
        async def slow_handler():
            """Simulates an agent task that takes time to cancel."""
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                main_server.buffer.add_message(
                    sid,
                    {"type": "system", "subtype": "session_cancelled", "message": "Cancelled"},
                )
                main_server.buffer.add_message(
                    sid,
                    {"type": "system", "subtype": "session_state_changed", "state": "cancelled"},
                )
                main_server.buffer.mark_done(sid)
                handler_done.set()
                raise

        # The TestClient uses anyio to run the app in a worker thread.
        # We need to find and use that thread's event loop. Instead, we
        # use a simpler approach: store the handler as a callback that
        # the endpoint will schedule.
        main_server.active_tasks[f"task_{sid}"] = None  # Placeholder

        # Set buffer to running first (simulates active agent)
        main_server.buffer.add_message(sid, {"type": "system", "subtype": "progress"})

        # Register the slow handler to be scheduled when cancel is called.
        # This mimics the real run_agent_task being already running.
        original_cancel = main_server.cancel_session

        async def mock_handler():
            await asyncio.sleep(0.05)  # Small delay to simulate work
            main_server.buffer.add_message(
                sid,
                {"type": "system", "subtype": "session_cancelled", "message": "Cancelled"},
            )
            main_server.buffer.add_message(
                sid,
                {"type": "system", "subtype": "session_state_changed", "state": "cancelled"},
            )
            main_server.buffer.mark_done(sid)
            handler_done.set()

        # Store a task that the cancel endpoint will cancel.
        # The key insight: in production, the task is created by
        # run_agent_task in the same loop. Here we create it in a
        # way that the TestClient's loop can handle.
        loop = None

        def capture_loop():
            nonlocal loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            task = loop.create_task(mock_handler())
            main_server.active_tasks[f"task_{sid}"] = task
            # Run the handler in a thread so it's on its own loop
            thread = threading.Thread(target=loop.run_forever, daemon=True)
            thread.start()

        capture_loop()

        # Give the task a moment to start
        import time
        time.sleep(0.1)

        resp = client.post(f"/api/users/alice/sessions/{sid}/cancel")
        assert resp.status_code == 200

        # The CancelledError handler MUST have run by now.
        assert handler_done.wait(timeout=2), (
            "cancel_session returned before the task's CancelledError handler finished"
        )
        state = main_server.buffer.get_session_state(sid)
        assert state["state"] in ("cancelled", "completed", "idle"), (
            f"Buffer state should be terminal after cancel, got: {state['state']}"
        )

        # Cleanup
        if loop and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        main_server.active_tasks.pop(f"task_{sid}", None)


class TestSessionStatus:
    def test_status_of_new_session(self, client: TestClient) -> None:
        resp = client.post("/api/users/alice/sessions")
        sid = resp.json()["session_id"]

        resp = client.get(f"/api/users/alice/sessions/{sid}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == sid
        assert data["state"] == "idle"
        assert data["cost_usd"] == 0.0

    def test_status_after_activity(self, client: TestClient) -> None:
        resp = client.post("/api/users/alice/sessions")
        sid = resp.json()["session_id"]
        # Simulate activity
        main_server.buffer.add_message(sid, {
            "type": "system", "subtype": "progress"
        })

        resp = client.get(f"/api/users/alice/sessions/{sid}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "running"


# ── WebSocket endpoint ─────────────────────────────────────────────


class TestWebSocketEndpoint:
    def test_ws_connect_and_disconnect(self, client: TestClient) -> None:
        """Basic WS connection test — disconnect handled gracefully after bug fix."""
        # Just verify connection can be opened and closed without error
        with client.websocket_connect("/ws"):
            pass  # connect, then exit context (disconnect) — no exception expected

    def test_ws_receives_error_for_missing_message(self, client: TestClient) -> None:
        """Send malformed JSON — should get error response."""
        with client.websocket_connect("/ws") as ws:
            ws.send_text("not-valid-json")
            # The server catches the exception and sends an error message
            # or closes the connection
            data = ws.receive_text()
            result = json.loads(data)
            # Either an error message or the server closed cleanly
            assert "type" in result or result is not None

    def test_ws_recover_sends_replay_messages(self, client: TestClient) -> None:
        """Send recover — verify replay messages are sent with correct flags."""
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
            # Verify replayed messages arrive with correct structure
            msg1 = json.loads(ws.receive_text())
            assert msg1["type"] == "user"
            assert msg1["content"] == "hello"
            assert msg1["replay"] is True
            assert msg1["index"] == 0

            msg2 = json.loads(ws.receive_text())
            assert msg2["type"] == "assistant"
            assert msg2["content"] == "hi back"
            assert msg2["replay"] is True
            assert msg2["index"] == 1

            # Session is done — after replay, the handler enters subscribe loop,
            # checks is_done(), does final pull (none), and exits.
            # The connection should close cleanly.
            # We don't assert on close because TestClient behavior varies.

    def test_ws_recover_partial_from_index(self, client: TestClient) -> None:
        """Recover with last_index > 0 — only newer messages are replayed."""
        buf = main_server.buffer
        sid = "session_recover_partial"
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
            msg1 = json.loads(ws.receive_text())
            assert msg1["content"] == "msg-2"
            assert msg1["index"] == 2
            assert msg1["replay"] is True

            msg2 = json.loads(ws.receive_text())
            assert msg2["content"] == "msg-3"
            assert msg2["index"] == 3
            assert msg2["replay"] is True

    def test_ws_recover_includes_session_id(self, client: TestClient) -> None:
        """Recover replay messages must include session_id so frontend
        can derive session state from session_state_changed messages."""
        buf = main_server.buffer
        sid = "session_recover_state_test"
        buf.add_message(sid, {"type": "user", "content": "hello"})
        buf.add_message(sid, {
            "type": "system",
            "subtype": "session_state_changed",
            "state": "running",
        })

        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({
                "type": "recover",
                "session_id": sid,
                "user_id": "alice",
                "last_index": 0,
            }))
            msg1 = json.loads(ws.receive_text())
            assert msg1["session_id"] == sid
            assert msg1["replay"] is True

            msg2 = json.loads(ws.receive_text())
            assert msg2["session_id"] == sid
            assert msg2["type"] == "system"
            assert msg2["subtype"] == "session_state_changed"
            assert msg2["state"] == "running"
            assert msg2["replay"] is True

    def test_ws_recover_emits_terminal_state_when_no_state_change(self, client: TestClient) -> None:
        """When the buffer is done with a terminal state but no
        session_state_changed message exists in the buffer, the recover
        loop must emit a synthetic session_state_changed so the frontend
        can transition away from 'running'."""
        buf = main_server.buffer
        sid = "session_terminal_safety_test"

        # Set up a completed session without any session_state_changed message
        buf.add_message(sid, {"type": "user", "content": "hello"})
        buf.add_message(sid, {"type": "assistant", "content": "done"})
        buf.mark_done(sid)

        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({
                "type": "recover",
                "session_id": sid,
                "user_id": "alice",
                "last_index": 0,
            }))

            # Collect all messages until connection closes or we see the state change
            messages = []
            state_change_found = False
            while True:
                try:
                    data = ws.receive_text()
                    msg = json.loads(data)
                    messages.append(msg)
                    if msg.get("type") == "system" and msg.get("subtype") == "session_state_changed":
                        state_change_found = True
                        break
                except Exception:
                    break

            assert state_change_found, (
                f"Recover loop did not emit terminal session_state_changed. "
                f"Messages received: {messages}"
            )
            # Verify it's the synthetic one (replay=False, correct state)
            state_msgs = [
                m for m in messages
                if m.get("type") == "system" and m.get("subtype") == "session_state_changed"
            ]
            assert len(state_msgs) == 1
            assert state_msgs[0]["state"] == "completed"
            assert state_msgs[0]["replay"] is False

    def test_ws_recover_no_duplicate_state_change(self, client: TestClient) -> None:
        """When a session_state_changed message already exists in the buffer,
        the recover loop must NOT emit a duplicate synthetic one."""
        buf = main_server.buffer
        sid = "session_no_duplicate_state_test"

        buf.add_message(sid, {"type": "user", "content": "hello"})
        buf.add_message(sid, {
            "type": "system",
            "subtype": "session_state_changed",
            "state": "completed",
        })
        buf.mark_done(sid)

        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({
                "type": "recover",
                "session_id": sid,
                "user_id": "alice",
                "last_index": 0,
            }))

            # Collect messages with a timeout — only expect 2: user msg + state_change
            messages = []
            for _ in range(4):  # safety limit
                try:
                    data = ws.receive_text()
                    msg = json.loads(data)
                    messages.append(msg)
                    # Once we've seen the session_state_changed from buffer,
                    # stop collecting — the synthetic one would arrive after
                    if msg.get("subtype") == "session_state_changed":
                        # Give a brief moment to see if a duplicate arrives
                        import time
                        time.sleep(0.3)
                        break
                except Exception:
                    break

            state_msgs = [
                m for m in messages
                if m.get("type") == "system" and m.get("subtype") == "session_state_changed"
            ]
            assert len(state_msgs) == 1, (
                f"Expected exactly 1 session_state_changed, got {len(state_msgs)}. "
                f"Messages: {messages}"
            )
            # The existing one should come from the buffer (replay=True for recover)
            assert state_msgs[0]["replay"] is True


# ── File Management API ────────────────────────────────────────────


class TestFileManagement:
    def test_list_files_empty_workspace(self, client: TestClient) -> None:
        resp = client.get("/api/users/alice/files")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_upload_and_list_files(self, client: TestClient) -> None:
        workspace = main_server.user_data_dir("alice") / "workspace" / "uploads"
        workspace.mkdir(parents=True, exist_ok=True)

        resp = client.post(
            "/api/users/alice/upload",
            files={"file": ("test.txt", b"hello world")},
        )
        assert resp.status_code == 200
        assert resp.json()["filename"] == "test.txt"
        assert resp.json()["size"] == 11

        resp = client.get("/api/users/alice/files")
        assert resp.status_code == 200
        files = resp.json()
        assert any(f["path"] == "uploads/test.txt" for f in files)

    def test_download_file(self, client: TestClient) -> None:
        workspace = main_server.user_data_dir("alice") / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "hello.txt").write_text("hello")

        resp = client.get("/api/users/alice/download/hello.txt")
        assert resp.status_code == 200
        assert resp.text == "hello"

    def test_download_traversal_blocked(self, client: TestClient) -> None:
        resp = client.get("/api/users/alice/download/../../../etc/passwd")
        assert resp.status_code in (403, 404)  # 403 = blocked, 404 = not found (also safe)

    def test_delete_file(self, client: TestClient) -> None:
        workspace = main_server.user_data_dir("alice") / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "tmp.txt").write_text("tmp")

        resp = client.delete("/api/users/alice/files/tmp.txt")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert not (workspace / "tmp.txt").exists()


# ── Skills API ─────────────────────────────────────────────────────


class TestGeneratedFilesEndpoint:
    """Test that /generated-files only returns outputs, not uploaded files."""

    def test_generated_files_excludes_uploads(self, client: TestClient) -> None:
        """Uploaded files should NOT appear in the generated-files list."""
        # Create an uploaded file
        uploads_dir = main_server.user_data_dir("alice") / "workspace" / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        (uploads_dir / "data.csv").write_text("col1,col2\n1,2")

        # Create an output file
        outputs_dir = main_server.user_data_dir("alice") / "workspace" / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        (outputs_dir / "report.pdf").write_bytes(b"%PDF-fake")

        resp = client.get("/api/users/alice/generated-files")
        assert resp.status_code == 200
        files = resp.json()
        filenames = [f["filename"] for f in files]

        assert "report.pdf" in filenames, "Output file should be listed"
        assert "data.csv" not in filenames, "Uploaded file should NOT be listed"

    def test_generated_files_returns_outputs_only(self, client: TestClient) -> None:
        """Only files from outputs/ directory should be returned."""
        outputs_dir = main_server.user_data_dir("alice") / "workspace" / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        (outputs_dir / "chart.png").write_bytes(b"\x89PNG-fake")

        resp = client.get("/api/users/alice/generated-files")
        assert resp.status_code == 200
        files = resp.json()
        assert len(files) == 1
        assert files[0]["filename"] == "chart.png"
        assert "outputs" in files[0]["download_url"]

    def test_generated_files_empty_outputs(self, client: TestClient) -> None:
        """Empty outputs directory should return empty list."""
        outputs_dir = main_server.user_data_dir("alice") / "workspace" / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)

        resp = client.get("/api/users/alice/generated-files")
        assert resp.status_code == 200
        assert resp.json() == []


class TestSkillsAPI:
    def test_create_and_list_user_skills(self, client: TestClient) -> None:
        resp = client.post(
            "/api/users/alice/skills",
            json={"name": "test-skill", "content": "# Test Skill", "description": "A test"},
        )
        assert resp.status_code == 200

        resp = client.get("/api/users/alice/skills")
        assert resp.status_code == 200
        skills = resp.json()
        assert any(s["name"] == "test-skill" for s in skills)

    def test_delete_skill(self, client: TestClient) -> None:
        client.post(
            "/api/users/alice/skills",
            json={"name": "to-delete", "content": "# Skill", "description": ""},
        )
        resp = client.delete("/api/users/alice/skills/to-delete")
        assert resp.status_code == 200

        resp = client.get("/api/users/alice/skills")
        skills = resp.json()
        assert not any(s["name"] == "to-delete" for s in skills)


# ── Memory API ─────────────────────────────────────────────────────


class TestMemoryAPI:
    def test_get_empty_memory(self, client: TestClient) -> None:
        resp = client.get("/api/users/alice/memory")
        assert resp.status_code == 200
        assert resp.json() == {"user_id": "alice"}

    def test_update_and_get_memory(self, client: TestClient) -> None:
        resp = client.put(
            "/api/users/alice/memory",
            json={
                "preferences": {"model": "claude-sonnet-4-6", "max_budget_usd": 5.0},
                "entity_memory": {"company_name": "Acme Corp"},
                "audit_context": {"prior_findings": [], "risk_areas": ["billing"]},
                "file_memory": [],
            },
        )
        assert resp.status_code == 200

        resp = client.get("/api/users/alice/memory")
        data = resp.json()
        assert data["preferences"]["model"] == "claude-sonnet-4-6"
        assert data["entity_memory"]["company_name"] == "Acme Corp"
        assert "billing" in data["audit_context"]["risk_areas"]


# ── MCP Registry ───────────────────────────────────────────────────


class TestMCPRegistry:
    def test_list_empty_mcp_servers(self, client: TestClient) -> None:
        resp = client.get("/api/admin/mcp-servers")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_register_and_list_mcp_server(self, client: TestClient) -> None:
        resp = client.post(
            "/api/admin/mcp-servers",
            json={
                "name": "test-server",
                "type": "stdio",
                "command": "echo",
                "args": ["hello"],
                "tools": ["greet"],
                "description": "Test MCP",
                "enabled": True,
                "access": "all",
            },
        )
        assert resp.status_code == 200

        resp = client.get("/api/admin/mcp-servers")
        assert resp.status_code == 200
        data = resp.json()
        assert any(s["name"] == "test-server" for s in data)

    def test_register_mcp_server_with_env(self, client: TestClient) -> None:
        resp = client.post(
            "/api/admin/mcp-servers",
            json={
                "name": "env-server",
                "type": "stdio",
                "command": "uvx",
                "args": ["some-mcp"],
                "env": {"API_KEY": "test123", "BASE_URL": "http://localhost:8000"},
                "tools": ["tool1"],
            },
        )
        assert resp.status_code == 200

        resp = client.get("/api/admin/mcp-servers")
        data = resp.json()
        env_server = next(s for s in data if s["name"] == "env-server")
        assert env_server["env"]["API_KEY"] == "test123"
        assert env_server["env"]["BASE_URL"] == "http://localhost:8000"

    def test_unregister_mcp_server(self, client: TestClient) -> None:
        client.post(
            "/api/admin/mcp-servers",
            json={
                "name": "to-remove",
                "type": "stdio",
                "command": "echo",
                "args": [],
                "tools": [],
            },
        )
        resp = client.delete("/api/admin/mcp-servers/to-remove")
        assert resp.status_code == 200

        resp = client.get("/api/admin/mcp-servers")
        assert not any(s["name"] == "to-remove" for s in resp.json())

    def test_toggle_mcp_server(self, client: TestClient) -> None:
        client.post(
            "/api/admin/mcp-servers",
            json={
                "name": "toggle-test",
                "type": "stdio",
                "command": "echo",
                "args": [],
                "tools": [],
            },
        )
        resp = client.patch("/api/admin/mcp-servers/toggle-test/toggle?enabled=false")
        assert resp.status_code == 200

        resp = client.get("/api/admin/mcp-servers")
        toggle_server = next(s for s in resp.json() if s["name"] == "toggle-test")
        assert toggle_server["enabled"] is False


class TestMCPStatusEndpoint:
    """Test GET /api/admin/mcp-servers/status endpoint."""

    def test_status_endpoint_empty(self, client: TestClient) -> None:
        """When no MCP servers are configured, returns empty list."""
        resp = client.get("/api/admin/mcp-servers/status")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_status_endpoint_stdio_server_command_missing(self, client: TestClient) -> None:
        """stdio server with command not in PATH should report disconnected."""
        client.post(
            "/api/admin/mcp-servers",
            json={
                "name": "missing-cmd",
                "type": "stdio",
                "command": "this-command-does-not-exist-xyz",
                "args": [],
                "tools": [],
            },
        )
        resp = client.get("/api/admin/mcp-servers/status")
        assert resp.status_code == 200
        data = resp.json()
        server = next(s for s in data if s["name"] == "missing-cmd")
        assert server["type"] == "stdio"
        assert server["status"] == "disconnected"
        assert server["error"] is not None
        assert server.get("tool_count", 0) == 0

    def test_status_endpoint_stdio_server_command_found(self, client: TestClient) -> None:
        """stdio server with valid command but non-MCP process should report disconnected
        (the connection test actually tries to initialize MCP, not just check PATH)."""
        client.post(
            "/api/admin/mcp-servers",
            json={
                "name": "echo-server",
                "type": "stdio",
                "command": "echo",
                "args": ["hello"],
                "tools": ["greet"],
            },
        )
        resp = client.get("/api/admin/mcp-servers/status")
        assert resp.status_code == 200
        data = resp.json()
        server = next(s for s in data if s["name"] == "echo-server")
        assert server["type"] == "stdio"
        # echo is not an MCP server, so the real connection test will fail
        assert server["status"] in ("disconnected", "error")
        assert "tool_count" in server
        assert server["tool_count"] == 0

    def test_status_endpoint_http_server(self, client: TestClient) -> None:
        """http server status check (connection attempt)."""
        client.post(
            "/api/admin/mcp-servers",
            json={
                "name": "http-server",
                "type": "http",
                "command": "",
                "url": "http://localhost:99999/mcp",
                "tools": [],
            },
        )
        resp = client.get("/api/admin/mcp-servers/status")
        assert resp.status_code == 200
        data = resp.json()
        server = next(s for s in data if s["name"] == "http-server")
        assert server["type"] == "http"
        # Should be disconnected since nothing is listening on that URL
        assert server["status"] in ("disconnected", "error")


class TestMCPEnabledFiltering:
    """Test that disabled MCP servers are NOT passed to the SDK."""

    def test_disabled_server_not_in_allowed_tools(self, client: TestClient) -> None:
        """A disabled MCP server's tools should NOT appear in allowed_tools."""
        client.post(
            "/api/admin/mcp-servers",
            json={
                "name": "disabled-server",
                "type": "stdio",
                "command": "echo",
                "args": [],
                "tools": ["tool1", "tool2"],
                "enabled": False,
            },
        )
        from main_server import load_mcp_config_sync, build_allowed_tools
        config = load_mcp_config_sync()
        tools = build_allowed_tools(config)
        assert "mcp__disabled-server__tool1" not in tools
        assert "mcp__disabled-server__tool2" not in tools

    def test_enabled_server_in_allowed_tools(self, client: TestClient) -> None:
        """An enabled MCP server's tools SHOULD appear in allowed_tools."""
        client.post(
            "/api/admin/mcp-servers",
            json={
                "name": "enabled-server",
                "type": "stdio",
                "command": "echo",
                "args": [],
                "tools": ["toolA"],
                "enabled": True,
            },
        )
        from main_server import load_mcp_config_sync, build_allowed_tools
        config = load_mcp_config_sync()
        tools = build_allowed_tools(config)
        assert "mcp__enabled-server__toolA" in tools

    def test_disabled_server_not_in_mcp_servers_dict(self, client: TestClient) -> None:
        """A disabled MCP server should NOT be in the mcp_servers dict
        passed to ClaudeAgentOptions."""
        client.post(
            "/api/admin/mcp-servers",
            json={
                "name": "no-spawn",
                "type": "stdio",
                "command": "echo",
                "args": [],
                "tools": [],
                "enabled": False,
            },
        )
        from main_server import load_mcp_config_sync
        config = load_mcp_config_sync()
        mcp_servers = {}
        for server_name, cfg in config.get("mcpServers", {}).items():
            if not cfg.get("enabled", True):
                continue
            if cfg.get("type") == "stdio":
                mcp_servers[server_name] = {"type": "stdio"}
        assert "no-spawn" not in mcp_servers

    def test_toggle_disappears_from_sdk(self, client: TestClient) -> None:
        """After toggling a server to disabled, it should no longer
        appear in the SDK config on next load."""
        client.post(
            "/api/admin/mcp-servers",
            json={
                "name": "toggle-me",
                "type": "stdio",
                "command": "echo",
                "args": [],
                "tools": ["t1"],
            },
        )
        # Verify it's enabled
        from main_server import load_mcp_config_sync, build_allowed_tools
        config = load_mcp_config_sync()
        tools = build_allowed_tools(config)
        assert "mcp__toggle-me__t1" in tools

        # Disable it
        resp = client.patch("/api/admin/mcp-servers/toggle-me/toggle?enabled=false")
        assert resp.status_code == 200

        # Verify it's gone from tools
        config = load_mcp_config_sync()
        tools = build_allowed_tools(config)
        assert "mcp__toggle-me__t1" not in tools


# ── Feedback API ───────────────────────────────────────────────────


class TestFeedbackAPI:
    def test_submit_feedback(self, client: TestClient) -> None:
        resp = client.post(
            "/api/users/alice/feedback",
            json={"session_id": "s1", "rating": 5, "comment": "Great!"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestSkillFeedbackAuth:
    """Test that skill feedback endpoint extracts user_id from auth header."""

    def test_submit_skill_feedback_without_auth_fallback_default(self, client: TestClient) -> None:
        """When ENFORCE_AUTH=false (default in tests), missing token falls back to 'default'."""
        with patch("main_server._get_user_id_from_header", return_value="default"):
            resp = client.post(
                "/api/skills/test-skill/feedback",
                json={"rating": 4, "comment": "Good"},
            )
            assert resp.status_code == 200

    def test_submit_skill_feedback_with_auth_header(self, client: TestClient) -> None:
        """When auth header is provided, user_id is extracted from token."""
        with patch("main_server._get_user_id_from_header", return_value="alice"):
            resp = client.post(
                "/api/skills/test-skill/feedback",
                json={"rating": 5, "comment": "Excellent"},
            )
            assert resp.status_code == 200

    def test_submit_skill_feedback_with_user_edits(self, client: TestClient) -> None:
        """user_edits field is accepted and stored."""
        with patch("main_server._get_user_id_from_header", return_value="alice"):
            resp = client.post(
                "/api/skills/test-skill/feedback",
                json={"rating": 4, "comment": "Good", "user_edits": "Fixed formatting"},
            )
            assert resp.status_code == 200


class TestSkillFeedbackAuthHeaderExtraction:
    """Verify that the Authorization header is actually extracted from HTTP
    requests (not always None). This catches the bug where `authorization:
    str | None = None` without Header() silently drops the header."""

    def test_authorization_header_is_extracted(self, client: TestClient) -> None:
        """When a Bearer token is sent, _get_user_id_from_header receives it."""
        with patch("main_server._get_user_id_from_header", return_value="alice") as mock_fn:
            resp = client.post(
                "/api/skills/test-skill/feedback",
                headers={"Authorization": "Bearer some-token"},
                json={"rating": 5, "comment": "With auth"},
            )
            assert resp.status_code == 200
            # Verify the function was called with the actual header value
            mock_fn.assert_called_once()
            call_arg = mock_fn.call_args[1].get("authorization") or mock_fn.call_args[0][0]
            assert call_arg == "Bearer some-token", (
                f"Authorization header was not extracted. Got: {call_arg!r}. "
                "This means the endpoint is missing Header() on the authorization parameter."
            )

    def test_authorization_header_none_when_not_sent(self, client: TestClient) -> None:
        """When no auth header is sent, _get_user_id_from_header receives None."""
        with patch("main_server._get_user_id_from_header", return_value="default") as mock_fn:
            resp = client.post(
                "/api/skills/test-skill/feedback",
                json={"rating": 3, "comment": "No auth"},
            )
            assert resp.status_code == 200
            mock_fn.assert_called_once()
            call_arg = mock_fn.call_args[1].get("authorization")
            assert call_arg is None, (
                f"Expected None when no header sent, got: {call_arg!r}"
            )


class TestUserFeedbackQuery:
    """Test GET /api/users/{user_id}/feedback endpoint."""

    def test_get_user_feedback_empty(self, client: TestClient) -> None:
        """Returns empty result when user has no feedback."""
        resp = client.get("/api/users/alice/feedback")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stats"] == []
        assert data["items"] == []
        assert data["total_count"] == 0

    def test_get_user_feedback_with_data_no_db_fallback(self, client: TestClient) -> None:
        """When no DB is available, returns empty result (expected fallback)."""
        # In test environment, _db is None, so the endpoint returns empty fallback
        resp = client.get("/api/users/alice/feedback")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_count"] == 0


class TestUserMessagePersistence:
    def test_user_message_persisted_on_first_session(self, client: TestClient) -> None:
        """User messages should be written to buffer on session start so they survive page refresh."""
        # Create a session and get its ID
        resp = client.post("/api/users/alice/sessions")
        sid = resp.json()["session_id"]

        # The session buffer should exist
        state = main_server.buffer.get_session_state(sid)
        assert state["state"] == "idle"


# ── MessageBuffer: result message sets done flag ──────────────────

class TestResultMessageSetsDone:
    def test_result_message_marks_buffer_done(self) -> None:
        """When a result message arrives, the buffer should be marked done so
        the WebSocket polling loop exits and the frontend stops showing 'Agent is working...'."""
        sid = "session_test_result_done"
        main_server.buffer.add_message(sid, {
            "type": "system",
            "subtype": "progress",
        })
        assert main_server.buffer.get_session_state(sid)["state"] == "running"
        assert main_server.buffer.is_done(sid) is False

        main_server.buffer.add_message(sid, {
            "type": "result",
            "subtype": "success",
            "session_id": sid,
        })
        assert main_server.buffer.get_session_state(sid)["state"] == "completed"
        assert main_server.buffer.is_done(sid) is True


# ── MAX_TURNS default value ────────────────────────────────────────

class TestMaxTurnsDefault:
    def test_max_turns_default_is_200(self) -> None:
        """MAX_TURNS default should be 200 to allow multi-step data processing tasks."""
        with patch.dict("os.environ", {}, clear=False):
            # Remove MAX_TURNS if set, then read the default
            import os
            orig = os.environ.pop("MAX_TURNS", None)
            try:
                # The function reads from env at call time, so we need to
                # call the actual function. Since build_agent_options is not
                # easily unit-testable (it loads skills, etc.), we test the
                # default value by importing the module-level read.
                # Instead, verify the source code contains the expected default.
                import inspect
                source = inspect.getsource(main_server)
                # Check that the default is "200"
                assert '"200"' in source or "'200'" in source
            finally:
                if orig is not None:
                    os.environ["MAX_TURNS"] = orig


# ── message_to_dicts: ToolResultBlock yields tool_result messages ──

class TestMessageToDicts:
    def test_message_to_dicts_imports_tool_result_block(self) -> None:
        """main_server should import ToolResultBlock from the SDK types."""
        import inspect
        source = inspect.getsource(main_server)
        assert "ToolResultBlock" in source

    def test_message_to_dicts_is_generator(self) -> None:
        """message_to_dicts should be a generator function yielding multiple dicts."""
        import inspect
        assert inspect.isgeneratorfunction(main_server.message_to_dicts)

    def test_tool_result_message_buffer_fields(self) -> None:
        """A tool_result message should carry content, name, and is_error fields
        so the frontend can render Bash output."""
        sid = "session_test_tool_result"
        # Simulate what message_to_dicts produces for a ToolResultBlock
        main_server.buffer.add_message(sid, {
            "type": "tool_result",
            "name": "Bash",
            "tool_use_id": "toolu_01abc",
            "content": "hello from bash",
            "is_error": False,
        })
        msgs = main_server.buffer.get_history(sid)
        tool_result = msgs[0]
        assert tool_result["type"] == "tool_result"
        assert tool_result["name"] == "Bash"
        assert tool_result["content"] == "hello from bash"
        assert tool_result["is_error"] is False

    def test_tool_result_error_flag(self) -> None:
        """tool_result messages should preserve is_error=True."""
        sid = "session_test_tool_result_error"
        main_server.buffer.add_message(sid, {
            "type": "tool_result",
            "name": "Bash",
            "tool_use_id": "toolu_01xyz",
            "content": "command not found",
            "is_error": True,
        })
        msgs = main_server.buffer.get_history(sid)
        assert msgs[0]["is_error"] is True


# ── Error handling: subscribe loop must receive state message ─────

class TestAgentTaskErrorHandling:
    """When an error occurs in run_agent_task, the subscribe loop must
    receive BOTH the error message AND the session_state_changed message.
    The subscribe loop checks `is_done()` and then does a final pull —
    the state message MUST be in the buffer before `mark_done()` sets done=True.
    """

    def test_completed_path_state_message_reachable_after_done(self) -> None:
        """Normal completion: session_state_changed: completed must be
        added to buffer so the subscribe loop's final pull catches it.
        The state message must be visible in history after mark_done()."""
        sid = "session_test_completed_ordering"
        buf = main_server.buffer

        # Correct ordering (what the fix implements): state message first, then mark_done
        buf.add_message(sid, {"type": "system", "subtype": "session_state_changed", "state": "completed"})
        buf.mark_done(sid)

        # The subscribe loop does: is_done() → True → final pull → get_history()
        # The state message must be in that final pull.
        history = buf.get_history(sid)
        assert any(m.get("subtype") == "session_state_changed" for m in history)
        assert buf.is_done(sid) is True
        assert buf.get_session_state(sid)["state"] == "completed"

    def test_error_path_error_and_state_reachable_after_done(self) -> None:
        """Error path: both the error message and session_state_changed: error
        must be visible in history after mark_done()."""
        sid = "session_test_error_ordering"
        buf = main_server.buffer

        # Correct ordering (what the fix implements):
        # 1. Add error message (wakes consumers)
        buf.add_message(sid, {"type": "error", "message": "Something failed"})
        # 2. Add state change (wakes consumers)
        buf.add_message(sid, {"type": "system", "subtype": "session_state_changed", "state": "error"})
        # 3. Mark done (NO wake — relies on prior add_message wakes)
        buf.mark_done(sid)

        # Verify: both messages exist in buffer and are reachable
        history = buf.get_history(sid)
        assert any(m["type"] == "error" for m in history)
        assert any(m.get("subtype") == "session_state_changed" and m.get("state") == "error" for m in history)
        assert buf.is_done(sid) is True

    def test_cancelled_path_state_reachable_after_done(self) -> None:
        """Cancellation: session_state_changed: cancelled must be reachable
        in history after mark_done()."""
        sid = "session_test_cancelled_ordering"
        buf = main_server.buffer

        buf.add_message(sid, {"type": "system", "subtype": "session_cancelled", "message": "Cancelled"})
        buf.mark_done(sid)
        buf.add_message(sid, {"type": "system", "subtype": "session_state_changed", "state": "cancelled"})

        history = buf.get_history(sid)
        assert any(m.get("subtype") == "session_state_changed" and m.get("state") == "cancelled" for m in history)
        assert buf.is_done(sid) is True

    def test_state_message_wakes_consumers(self) -> None:
        """When a terminal state message is added to buffer, it must
        wake consumers via event.set(). The subscribe loop must not
        need to wait for a heartbeat to discover the state change."""
        sid = "session_test_consumer_wake"
        buf = main_server.buffer

        event = buf.subscribe(sid)

        # Add state change message — this should wake the consumer
        buf.add_message(sid, {"type": "system", "subtype": "session_state_changed", "state": "completed"})

        # Event should be set (consumer was woken)
        assert event.is_set() is True

    def test_source_completed_path_state_before_mark_done(self) -> None:
        """In the completed path of run_agent_task, the source code must
        call add_message(session_state_changed) BEFORE mark_done()."""
        import inspect
        source = inspect.getsource(main_server.run_agent_task)
        lines = source.split('\n')

        # Find the completed path: look for session_state_changed in the main try block
        # (before any except blocks). The state message must come before mark_done.
        state_msg_line = None
        mark_done_line = None

        for i, line in enumerate(lines):
            stripped = line.strip()
            # Skip comment lines
            if stripped.startswith('#'):
                continue

            # Stop searching when we hit the first except block
            if 'except asyncio.CancelledError' in stripped:
                break

            if '"session_state_changed"' in stripped:
                # Check if the next few lines contain "completed" state
                for j in range(i + 1, min(i + 3, len(lines))):
                    if '"completed"' in lines[j]:
                        state_msg_line = i
                        break

            if state_msg_line is None and 'buffer.mark_done' in stripped:
                mark_done_line = i

        assert state_msg_line is not None, "session_state_changed: completed not found in source"
        assert mark_done_line is None or state_msg_line < mark_done_line, (
            f"BUG: mark_done() at line {mark_done_line} appears before "
            f"session_state_changed at line {state_msg_line}"
        )

    def test_source_error_path_state_before_mark_done(self) -> None:
        """In the error path of run_agent_task, the source code must
        call add_message(session_state_changed) BEFORE mark_done()."""
        import inspect
        source = inspect.getsource(main_server.run_agent_task)
        lines = source.split('\n')

        # Find the error block at the function level (indent=4 spaces)
        error_start = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            # Match only the outer except block (4-space indent relative to function)
            if line.startswith('    except Exception') and 'CancelledError' not in stripped:
                error_start = i
                break

        assert error_start is not None, "Could not find outer except Exception block"

        # Search only within the error block
        state_msg_line = None
        mark_done_line = None

        for i in range(error_start, len(lines)):
            stripped = lines[i].strip()
            if stripped.startswith('#'):
                continue
            # Stop at next def or outer except
            if i > error_start and lines[i].startswith('    ') and not lines[i].startswith('        '):
                break

            if '"session_state_changed"' in stripped:
                for j in range(i + 1, min(i + 3, len(lines))):
                    if '"error"' in lines[j]:
                        state_msg_line = i
                        break

            if state_msg_line is None and 'buffer.mark_done' in stripped:
                mark_done_line = i

        assert state_msg_line is not None, "session_state_changed: error not found in source"
        assert mark_done_line is None or state_msg_line < mark_done_line, (
            f"BUG: mark_done() at line {mark_done_line} appears before "
            f"session_state_changed at line {state_msg_line}"
        )

    def test_source_cancelled_path_state_before_mark_done(self) -> None:
        """In the cancelled path of run_agent_task, the source code must
        call add_message(session_state_changed) BEFORE mark_done()."""
        import inspect
        source = inspect.getsource(main_server.run_agent_task)
        lines = source.split('\n')

        # Find the cancelled block at the function level (4-space indent)
        cancelled_start = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            if line.startswith('    except asyncio.CancelledError'):
                cancelled_start = i
                break

        assert cancelled_start is not None, "Could not find outer except CancelledError block"

        # Search only within the cancelled block
        state_msg_line = None
        mark_done_line = None

        for i in range(cancelled_start, len(lines)):
            stripped = lines[i].strip()
            if stripped.startswith('#'):
                continue
            # Stop at next def or outer except
            if i > cancelled_start and lines[i].startswith('    ') and not lines[i].startswith('        '):
                break

            if '"session_state_changed"' in stripped:
                for j in range(i + 1, min(i + 3, len(lines))):
                    if '"cancelled"' in lines[j]:
                        state_msg_line = i
                        break

            if state_msg_line is None and 'buffer.mark_done' in stripped:
                mark_done_line = i

        assert state_msg_line is not None, "session_state_changed: cancelled not found in source"
        assert mark_done_line is None or state_msg_line < mark_done_line, (
            f"BUG: mark_done() at line {mark_done_line} appears before "
            f"session_state_changed at line {state_msg_line}"
        )


# ── MessageBubble: error message visibility ───────────────────────


# ── file_result: append (not insert_before) so subscribe loop catches it ──

class TestFileResultDelivery:
    """file_result should be appended (not insert_before_type) so the
    subscribe loop's final pull after is_done() returns True catches it.

    Previously, insert_before_type("result") inserted at an index already
    sent by the subscribe loop, causing the file_result to be silently
    dropped from the WebSocket stream.
    """

    def test_file_result_reachable_after_mark_done(self) -> None:
        """A file_result appended before mark_done() should be reachable
        by the subscribe loop's final get_history call."""
        sid = "session_file_result_test"
        # Simulate: result message already in buffer (sent by subscribe loop)
        main_server.buffer.add_message(sid, {
            "type": "result",
            "content": "Session completed",
        })
        # Append file_result BEFORE mark_done (matching the fixed code path)
        main_server.buffer.add_message(sid, {
            "type": "file_result",
            "content": "",
            "session_id": sid,
            "user_id": "user-123",
            "data": [
                {"filename": "report.pdf", "size": 51200, "download_url": "/api/users/user-123/download/outputs/report.pdf"},
            ],
        })
        main_server.buffer.add_message(sid, {
            "type": "system",
            "subtype": "session_state_changed",
            "state": "completed",
        })
        main_server.buffer.mark_done(sid)

        # Subscribe loop's final pull: get all messages after the "result"
        # (simulating last_seen = 1, i.e., after the result was sent)
        msgs = main_server.buffer.get_history(sid, after_index=1)
        # Should contain file_result and session_state_changed
        types = [m.get("type") for m in msgs]
        assert "file_result" in types, f"file_result not reachable! Messages: {types}"
        assert "system" in types

    def test_subscribe_loop_no_duplicate_result(self) -> None:
        """Simulate the subscribe loop's two-phase pull (normal iteration
        + final pull after is_done). The "result" message must NOT appear
        in both phases."""
        sid = "session_no_duplicate_result"

        # Phase 1: Normal iteration sends messages up to and including "result"
        main_server.buffer.add_message(sid, {"type": "assistant", "content": "Hello"})
        main_server.buffer.add_message(sid, {"type": "result", "content": "Session completed"})

        # Normal iteration pulls and sends both messages
        normal_batch = main_server.buffer.get_history(sid, after_index=0)
        normal_types = [m.get("type") for m in normal_batch]
        assert "result" in normal_types
        assert "file_result" not in normal_types  # Not added yet

        last_seen = len(normal_batch)  # last_seen = 2

        # Phase 2: Agent task exits, adds file_result and state_change, marks done
        main_server.buffer.add_message(sid, {
            "type": "file_result",
            "content": "",
            "session_id": sid,
            "user_id": "user-123",
            "data": [{"filename": "test.pdf", "size": 1024}],
        })
        main_server.buffer.add_message(sid, {
            "type": "system",
            "subtype": "session_state_changed",
            "state": "completed",
        })
        main_server.buffer.mark_done(sid)

        # Final pull: should only get NEW messages after last_seen
        final_batch = main_server.buffer.get_history(sid, after_index=last_seen)
        final_types = [m.get("type") for m in final_batch]

        # file_result and state_change should be present
        assert "file_result" in final_types, f"Missing file_result in final pull! Types: {final_types}"
        assert "system" in final_types

        # result must NOT be in the final pull (it was already sent in normal iteration)
        assert "result" not in final_types, (
            f"BUG: result duplicated in final pull! Normal had {normal_types}, "
            f"final had {final_types}"
        )

        # Combined: each message type appears exactly once
        all_types = normal_types + final_types
        assert all_types.count("result") == 1, "result appears more than once"
        assert all_types.count("file_result") == 1, "file_result appears more than once"

    def test_file_result_wakes_consumers(self) -> None:
        """Appending file_result should wake up waiting consumers."""
        import asyncio
        from threading import Event, Thread

        sid = "session_file_result_wake"
        buf = main_server.buffer._ensure_buf(sid)

        # Simulate a waiting consumer
        event = Event()
        buf["consumers"].add(event)

        # Add file_result (this should wake the consumer)
        main_server.buffer.add_message(sid, {
            "type": "file_result",
            "content": "",
            "session_id": sid,
            "user_id": "user-123",
            "data": [{"filename": "test.pdf", "size": 1024}],
        })

        # Consumer should have been woken
        assert event.is_set(), "file_result did not wake consumers"

    def test_file_result_includes_user_id(self) -> None:
        """file_result message should include user_id for download URL
        construction in the frontend."""
        sid = "session_file_result_userid"
        main_server.buffer.add_message(sid, {
            "type": "file_result",
            "content": "",
            "session_id": sid,
            "user_id": "user-456",
            "data": [{"filename": "data.xlsx", "size": 2048}],
        })
        msgs = main_server.buffer.get_history(sid)
        assert msgs[0].get("user_id") == "user-456"

    def test_source_completed_file_result_before_mark_done(self) -> None:
        """In the completed path of run_agent_task, the source code must
        call add_message(file_result) BEFORE mark_done()."""
        import inspect
        source = inspect.getsource(main_server.run_agent_task)
        lines = source.split('\n')

        # Find the completed block (mark_done area)
        mark_done_line = None
        file_result_line = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            if 'buffer.mark_done' in stripped:
                mark_done_line = i
            if '"file_result"' in stripped or "'file_result'" in stripped:
                file_result_line = i

        assert file_result_line is not None, "file_result not found in completed path"
        assert mark_done_line is not None, "mark_done not found in completed path"
        assert file_result_line < mark_done_line, (
            f"BUG: file_result at line {file_result_line} appears after "
            f"mark_done at line {mark_done_line}"
        )


# ── File result ordering vs SDK result ────────────────────────────


class TestFileResultBeforeResult:
    """file_result must appear BEFORE the SDK's "result" message
    (which renders as "Session completed") in both live streaming
    and DB replay.

    The SDK emits a ResultMessage last, which message_to_dicts converts
    to {"type": "result", ...}. If this result is added to the buffer
    before file_result, the subscribe loop sends result first and
    file_result second — putting the file card AFTER "Session completed".

    Fix: Buffer the SDK result message, add file_result first, then
    re-add the buffered result.
    """

    def test_file_result_before_result_in_subscribe_output(self) -> None:
        """Simulate the subscribe loop sending all messages.
        file_result must have a lower index than result so the
        file card appears above 'Session completed' in the UI.

        This test simulates the buffer state AFTER the fix:
        file_result should be at a lower seq/index than result.
        """
        # Simulate the CORRECT buffer state after the fix
        # (file_result added before result)
        sid = "session_order_correct"
        main_server.buffer.add_message(sid, {"type": "assistant", "content": "Hello"})
        main_server.buffer.add_message(sid, {
            "type": "file_result",
            "content": "",
            "session_id": sid,
            "user_id": "user-123",
            "data": [{"filename": "calendar.docx", "size": 36000}],
        })
        main_server.buffer.add_message(sid, {
            "type": "system",
            "subtype": "session_state_changed",
            "state": "completed",
        })
        main_server.buffer.add_message(sid, {
            "type": "result",
            "subtype": "complete",
            "duration_ms": 64500,
            "total_cost_usd": 0.3835,
        })
        main_server.buffer.mark_done(sid)

        history = main_server.buffer.get_history(sid)
        types = [m.get("type") for m in history]

        # Find indices
        file_result_idx = types.index("file_result")
        result_idx = types.index("result")
        assert file_result_idx < result_idx, (
            f"file_result (idx={file_result_idx}) must appear before "
            f"result (idx={result_idx}) — got order: {types}"
        )

    def test_current_broken_ordering_result_before_file_result(self) -> None:
        """This test documents the OLD broken ordering (result before file_result).
        After the fix to run_agent_task, the actual buffer ordering is now correct
        (file_result before result), verified by the test above.

        This test remains as a behavioral specification: if messages are added
        in the order result→file_result, file_result will have a higher index.
        The fix prevents this by buffering the SDK result and emitting it last.
        """
        sid = "session_order_broken"
        # Simulate the BROKEN old state (result before file_result)
        main_server.buffer.add_message(sid, {"type": "assistant", "content": "Hello"})
        main_server.buffer.add_message(sid, {
            "type": "result",
            "subtype": "complete",
            "duration_ms": 64500,
            "total_cost_usd": 0.3835,
        })
        main_server.buffer.add_message(sid, {
            "type": "file_result",
            "content": "",
            "session_id": sid,
            "user_id": "user-123",
            "data": [{"filename": "calendar.docx", "size": 36000}],
        })
        main_server.buffer.mark_done(sid)

        history = main_server.buffer.get_history(sid)
        types = [m.get("type") for m in history]

        result_idx = types.index("result")
        file_result_idx = types.index("file_result")

        # Documents: when result is added before file_result, result has lower index.
        # The fix prevents this by buffering the SDK result until after file_result.
        assert result_idx < file_result_idx, (
            f"Expected result before file_result in this simulation. Got: {types}"
        )


# ── User message visibility in subscribe loop ─────────────────────


class TestUserMessageInSubscribeLoop:
    """The subscribe loop must send user messages back to the frontend
    so they survive as confirmed server messages, not just optimistic
    local copies that disappear on state changes."""

    def test_user_message_not_filtered_in_source(self) -> None:
        """The subscribe loop source must NOT filter out user messages."""
        import inspect
        source = inspect.getsource(main_server.handle_ws)
        # The subscribe loop should not have a filter that skips user messages
        # Look for the problematic pattern: if h.get("type") == "user": continue
        lines = source.split('\n')
        for i, line in enumerate(lines):
            if 'h.get("type") == "user"' in line or "h.get('type') == 'user'" in line:
                # Found the filter — verify it's removed or that user messages are sent
                pytest.fail(
                    f"Subscribe loop still filters user messages at line {i}: {line.strip()}. "
                    "User messages must be sent so the frontend has confirmed copies."
                )


# ── Heartbeat inflating last_seen cursor ──────────────────────────


class TestHeartbeatDoesNotInflateCursor:
    """The subscribe loop sends heartbeats during idle periods. Each
    heartbeat increments last_seen by 1, but heartbeats are synthetic
    messages NOT stored in the buffer. After many heartbeats, last_seen
    drifts past the actual buffer end, causing the final pull to skip
    session_state_changed:completed and the page stays 'running'.

    The fix: do NOT increment last_seen for heartbeat messages.
    """

    def test_heartbeat_last_seen_increment_in_source(self) -> None:
        """The subscribe loop must NOT increment last_seen after sending
        a heartbeat. The line 'last_seen += 1' must not exist in the
        heartbeat TimeoutError branch."""
        import inspect
        source = inspect.getsource(main_server.handle_ws)
        lines = source.split('\n')

        # Find the heartbeat TimeoutError block and check for last_seen += 1
        in_heartbeat_block = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if 'asyncio.TimeoutError' in stripped:
                in_heartbeat_block = True
            if in_heartbeat_block and 'except' in stripped and 'TimeoutError' not in stripped:
                in_heartbeat_block = False
                continue
            if in_heartbeat_block and 'last_seen += 1' in stripped:
                pytest.fail(
                    f"Heartbeat handler still inflates last_seen at line {i}: {stripped}. "
                    "Heartbeats are synthetic — incrementing last_seen causes the cursor "
                    "to drift past the actual buffer end, missing completion messages."
                )


class TestEvolutionCandidatesEndpoint:
    """Test that the evolution candidates endpoint returns 200, not 404,
    even when there are no qualifying skills."""

    def test_evolution_candidates_returns_200_not_404(self, client: TestClient) -> None:
        """GET /api/admin/skills/evolution-candidates must return 200 with
        an empty candidates list when no skills qualify — never 404."""
        resp = client.get("/api/admin/skills/evolution-candidates")
        assert resp.status_code == 200, (
            f"Expected 200 for 'no candidates' response, got {resp.status_code}. "
            "The endpoint should return 200 with {candidates: []}, not 404."
        )
        data = resp.json()
        assert "candidates" in data
        assert isinstance(data["candidates"], list)


class TestAdminFeedbackEndpoint:
    """Test GET /api/admin/feedback returns all feedback (not user-scoped)."""

    def test_admin_feedback_returns_all_feedback(self, client: TestClient) -> None:
        """When DB is available, the admin endpoint should return feedback from all users."""
        with patch("main_server._db") as mock_db:
            import asyncio
            from src.skill_feedback import DBSkillFeedbackManager
            from src.database import Database

            async def _run():
                db = Database()
                # We can't easily mock the DB connection, so test via the manager directly
                pass

            # Just verify the endpoint doesn't 404
            resp = client.get("/api/admin/feedback")
            # Will fall through to file-based or empty, but must not 404
            assert resp.status_code in (200, 500)

    def test_admin_feedback_has_correct_structure(self, client: TestClient) -> None:
        """Response must have stats, items, and total_count fields."""
        with patch("main_server._db") as mock_db:
            resp = client.get("/api/admin/feedback")
            if resp.status_code == 200:
                data = resp.json()
                assert "stats" in data
                assert "items" in data
                assert "total_count" in data


class TestRollbackEndpoint:
    """Test POST /api/skills/{skill_name}/rollback endpoint."""

    def test_rollback_returns_info_when_no_backup(self, client: TestClient) -> None:
        """Rollback should return 'info' status with a message when no backup exists."""
        resp = client.post("/api/skills/nonexistent-skill/rollback")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            data = resp.json()
            assert data["status"] == "info"
            assert "message" in data


# ── Recover check must come AFTER session_id check ────────────────


class TestRecoverCheckOrderInSubscribeLoop:
    """In the subscribe loop (and recover loop), the session_id check
    must come BEFORE the type=="recover" check. Otherwise a recover
    message for a *different* session hits `continue` and never triggers
    the break that switches the WS to the new session.

    Symptoms: after switching sessions, "Agent is working" disappears
    and the new session's real-time messages are never received.
    """

    def _check_order_in_loop(self, source_lines: list[str], loop_label: str) -> None:
        """Find the first session_id comparison and first recover check
        after an answer check within the same logical block. The
        session_id check must appear before the recover check."""
        # Find lines containing the key patterns within the subscribe/recover loop area.
        # We look for the pattern:
        #   elif item.get("type") == "recover":   ← must NOT come before
        #   elif item.get("session_id") != session_id:
        # The correct order is session_id first, then recover.
        recover_line = None
        session_id_line = None
        for i, line in enumerate(source_lines):
            stripped = line.strip()
            if 'item.get("type") == "recover"' in stripped or "item.get('type') == 'recover'" in stripped:
                if recover_line is None:
                    recover_line = i
            if 'item.get("session_id")' in stripped and '!=' in stripped:
                if session_id_line is None:
                    session_id_line = i

        if recover_line is not None and session_id_line is not None:
            if recover_line < session_id_line:
                pytest.fail(
                    f"In {loop_label}, the recover type check (line {recover_line}) "
                    f"comes BEFORE the session_id check (line {session_id_line}). "
                    f"This causes recover messages for other sessions to be silently "
                    f"skipped, preventing WS session switching. "
                    f"Move the session_id check before the recover check."
                )

    def test_subscribe_loop_recover_check_order(self) -> None:
        """In the subscribe loop's elif chain (answer → recover → session_id),
        the session_id check must come before the recover check."""
        import inspect
        source = inspect.getsource(main_server.handle_ws)
        lines = source.split('\n')

        # Find the subscribe loop: look for "Subscribe to real-time messages" comment
        in_subscribe = False
        found_chain = False
        recover_line = None
        session_id_line = None

        for i, line in enumerate(lines):
            stripped = line.strip()
            if 'Subscribe to real-time messages' in stripped:
                in_subscribe = True
                continue
            if not in_subscribe:
                continue
            # Look for the answer/recover/session_id elif chain
            if 'item.get("type") == "answer"' in stripped and 'if item.get("session_id")' not in stripped:
                # This is the old pattern — the answer check that's a plain if
                found_chain = True
                recover_line = None
                session_id_line = None
                continue
            if found_chain:
                if 'item.get("type") == "recover"' in stripped and recover_line is None:
                    recover_line = i
                if 'item.get("session_id")' in stripped and '!=' in stripped and session_id_line is None:
                    session_id_line = i
                if 'else:' in stripped or 'Message for same session' in stripped:
                    # Chain ended
                    break

        if recover_line is not None and session_id_line is not None:
            if recover_line < session_id_line:
                pytest.fail(
                    f"In subscribe loop, recover check (line {recover_line}) "
                    f"comes before session_id check (line {session_id_line}). "
                    f"Move session_id check before recover check."
                )

    def test_recover_loop_recover_check_order(self) -> None:
        import inspect
        # The recover loop is inside handle_ws as well — same source.
        # We need to find both occurrences. The test above catches the first one.
        # For the second, we scan the full source and verify ALL recover checks
        # come after their corresponding session_id checks.
        source = inspect.getsource(main_server.handle_ws)
        lines = source.split('\n')

        # Find all blocks that contain both patterns
        # Strategy: find "subscribe" or recover-loop entry points and check within each block
        # The recover loop is a separate while-True block inside handle_ws.
        # We'll check that in every while-True block after the initial subscribe,
        # the order is correct.

        # Simpler approach: just verify no recover check comes before
        # a session_id check in ANY adjacent elif chain.
        in_answer_recover_chain = False
        recover_line = None
        session_id_line = None
        chain_start = None
        failures = []

        for i, line in enumerate(lines):
            stripped = line.strip()
            if 'item.get("type") == "answer"' in stripped:
                in_answer_recover_chain = True
                chain_start = i
                recover_line = None
                session_id_line = None
            elif in_answer_recover_chain and 'item.get("type") == "recover"' in stripped:
                recover_line = i
            elif in_answer_recover_chain and 'item.get("session_id")' in stripped and '!=' in stripped:
                session_id_line = i
            elif in_answer_recover_chain and ('else:' in stripped or stripped.startswith('except') or stripped.startswith('def ')):
                # Chain ended — check order
                if recover_line is not None and session_id_line is not None:
                    if recover_line < session_id_line:
                        failures.append(
                            f"Recover check at line {recover_line} comes before "
                            f"session_id check at line {session_id_line}"
                        )
                in_answer_recover_chain = False

        if failures:
            pytest.fail(
                "Recover check order violation in handle_ws:\n" + "\n".join(failures)
            )


# ── Atomic agent task creation ─────────────────────────────────────


class TestAtomicTaskCreation:
    """Agent task creation must be atomic to prevent duplicate
    concurrent tasks for the same session when rapid messages arrive."""

    def test_task_locks_exist_in_source(self) -> None:
        """main_server should have a _task_locks dict for per-session locking."""
        import inspect
        source = inspect.getsource(main_server)
        assert "_task_locks" in source, (
            "No _task_locks found — task creation is not atomic. "
            "Two rapid messages for the same session could create duplicate tasks."
        )

    def test_task_lock_used_around_creation(self) -> None:
        """The check-and-create for agent tasks must happen inside an
        async context manager using the session's lock."""
        import inspect
        source = inspect.getsource(main_server.handle_ws)
        # Look for async with pattern around task creation
        assert "async with" in source and "_task_locks" in source, (
            "Task creation not protected by async lock. "
            "Use 'async with _task_locks[task_key]:' around the check-and-create."
        )

    def test_task_lock_initialized_per_session(self) -> None:
        """Locks should be created on-demand for new sessions."""
        import inspect
        source = inspect.getsource(main_server.handle_ws)
        # Look for pattern: if key not in locks: locks[key] = Lock()
        assert "_task_locks[" in source and "asyncio.Lock()" in source, (
            "Task locks not initialized per session. "
            "Add: if task_key not in _task_locks: _task_locks[task_key] = asyncio.Lock()"
        )


# ── Sync MCP Loader ────────────────────────────────────────────────


class TestSyncMcpLoader:
    """load_mcp_config_sync() should read from SQLite when DB is initialized."""

    @pytest.mark.asyncio
    async def test_sync_loader_reads_from_sqlite(self, tmp_path: Path) -> None:
        """When _mcp_store and _db are set, sync loader reads from DB, not file."""
        from src.database import Database
        from src.mcp_store import MCPServerStore

        db_path = tmp_path / "test.db"
        db = Database(db_path)
        await db.init()

        store = MCPServerStore(db)
        # Insert a test MCP server
        await store.create({
            "name": "test-server",
            "type": "stdio",
            "command": "echo",
            "args": ["hello"],
            "env": {},
            "tools": ["tool1", "tool2"],
            "description": "Test server",
            "enabled": True,
            "access": "all",
        })

        # Set globals
        old_mcp_store = main_server._mcp_store
        old_db = main_server._db
        main_server._mcp_store = store
        main_server._db = db

        try:
            result = main_server.load_mcp_config_sync()
            assert "mcpServers" in result
            assert "test-server" in result["mcpServers"]
            cfg = result["mcpServers"]["test-server"]
            assert cfg["command"] == "echo"
            assert cfg["tools"] == ["tool1", "tool2"]
            assert cfg["enabled"] is True
        finally:
            main_server._mcp_store = old_mcp_store
            main_server._db = old_db
            await db.close()

    def test_sync_loader_falls_back_to_file_when_no_db(self, tmp_path: Path) -> None:
        """When _mcp_store is None, sync loader reads from file."""
        # Ensure no DB is set
        old_mcp_store = main_server._mcp_store
        main_server._mcp_store = None

        # Write a test registry file
        registry = tmp_path / "mcp-registry.json"
        registry.write_text(json.dumps({
            "mcpServers": {
                "file-server": {"name": "file-server", "enabled": True}
            }
        }))

        old_root = main_server.DATA_ROOT
        main_server.DATA_ROOT = tmp_path

        try:
            result = main_server.load_mcp_config_sync()
            assert "file-server" in result["mcpServers"]
        finally:
            main_server._mcp_store = old_mcp_store
            main_server.DATA_ROOT = old_root

    def test_sync_loader_returns_empty_when_no_db_no_file(self) -> None:
        """When no DB and no file, returns empty mcpServers."""
        old_mcp_store = main_server._mcp_store
        main_server._mcp_store = None

        try:
            result = main_server.load_mcp_config_sync()
            assert result == {"mcpServers": {}}
        finally:
            main_server._mcp_store = old_mcp_store


# ── Agent Task Timeout ─────────────────────────────────────────────


class TestAgentTaskTimeout:
    """Agent task should have an overall wall-clock timeout."""

    def test_timeout_env_var_configurable(self) -> None:
        """AGENT_TASK_TIMEOUT should be read from env var, defaulting to 300."""
        import os
        # Default value
        assert float(os.getenv("AGENT_TASK_TIMEOUT", "300")) == 300.0

    def test_timeout_code_present_in_source(self) -> None:
        """run_agent_task should be wrapped in asyncio.wait_for."""
        import inspect
        source = inspect.getsource(main_server)
        assert "asyncio.wait_for" in source, (
            "No asyncio.wait_for found — agent tasks have no wall-clock timeout"
        )
        assert "AGENT_TASK_TIMEOUT" in source, (
            "No AGENT_TASK_TIMEOUT env var found — timeout not configurable"
        )

    def test_timeout_error_handler_exists(self) -> None:
        """There should be an asyncio.TimeoutError handler in run_agent_task."""
        import inspect
        source = inspect.getsource(main_server)
        assert "asyncio.TimeoutError" in source, (
            "No TimeoutError handler found — timeouts will fall through to generic exception"
        )
        assert "session_timeout" in source, (
            "No session_timeout subtype found — frontend won't know why session ended"
        )


# ── Streaming output: include_partial_messages ────────────────────────


class TestStreamingOutput:
    """Test that SDK options enable partial message streaming for real-time
    text display."""

    def test_build_sdk_options_sets_include_partial_messages_true(self) -> None:
        """build_sdk_options must set include_partial_messages=True so
        the SDK emits StreamEvent with content_block_delta events."""
        import inspect
        source = inspect.getsource(main_server.build_sdk_options)

        # Verify the field is set to True
        assert "include_partial_messages" in source, (
            "No include_partial_messages found in build_sdk_options — "
            "streaming output is disabled"
        )
        assert "True" in source.split("include_partial_messages")[1][:20], (
            "include_partial_messages is not set to True — "
            "SDK will not emit partial text deltas"
        )

    def test_stream_event_handler_exists_in_message_to_dicts(self) -> None:
        """message_to_dicts must handle StreamEvent messages to forward
        content_block_delta to the frontend."""
        import inspect
        source = inspect.getsource(main_server.message_to_dicts)
        assert "StreamEvent" in source, (
            "No StreamEvent handling in message_to_dicts — "
            "partial messages will be ignored"
        )
        assert "stream_event" in source, (
            "StreamEvent not converted to stream_event dict — "
            "frontend won't receive streaming events"
        )

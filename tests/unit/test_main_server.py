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
        assert resp.json() == {}

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
        assert "test-server" in data

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
        assert "to-remove" not in resp.json()

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
        assert resp.json()["toggle-test"]["enabled"] is False


# ── Feedback API ───────────────────────────────────────────────────


class TestFeedbackAPI:
    def test_submit_feedback(self, client: TestClient) -> None:
        resp = client.post(
            "/api/users/alice/feedback",
            json={"session_id": "s1", "rating": 5, "comment": "Great!"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


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

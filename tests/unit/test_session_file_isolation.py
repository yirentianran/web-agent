"""Tests for session-level file isolation in outputs/{session_id}/."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Mock claude_agent_sdk before importing main_server
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

import main_server

# Override auth enforcement (load_dotenv in main_server reads .env which sets ENFORCE_AUTH=true)
import src.auth
import src.admin_auth
src.auth.ENFORCE_AUTH = False
src.admin_auth.ENFORCE_AUTH = False
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _patch_data_root(tmp_path: Path) -> None:
    main_server.DATA_ROOT = tmp_path
    main_server.buffer = main_server.MessageBuffer()
    main_server.active_tasks.clear()
    main_server.pending_answers.clear()
    (tmp_path / "users").mkdir(exist_ok=True)
    for user in ("alice", "bob", "default"):
        (tmp_path / "users" / user).mkdir(exist_ok=True)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(main_server.app)


# ── Session directory creation ──────────────────────────────────────


class TestSessionDirCreation:
    def test_http_create_session_creates_output_dir(self, client: TestClient) -> None:
        """POST /api/users/{user_id}/sessions should create outputs/{session_id}/."""
        resp = client.post("/api/users/alice/sessions")
        assert resp.status_code == 200
        sid = resp.json()["session_id"]
        session_dir = main_server.DATA_ROOT / "users" / "alice" / "workspace" / "outputs" / sid
        assert session_dir.exists(), f"Expected {session_dir} to exist"

    def test_session_dir_is_idempotent(self, client: TestClient) -> None:
        """Creating a second session should not affect the first session's dir."""
        r1 = client.post("/api/users/alice/sessions")
        r2 = client.post("/api/users/alice/sessions")
        s1 = r1.json()["session_id"]
        s2 = r2.json()["session_id"]
        d1 = main_server.DATA_ROOT / "users" / "alice" / "workspace" / "outputs" / s1
        d2 = main_server.DATA_ROOT / "users" / "alice" / "workspace" / "outputs" / s2
        assert d1.exists()
        assert d2.exists()
        assert d1 != d2


# ── Scan workspace for generated files ──────────────────────────────


class TestScanWorkspaceForGeneratedFiles:
    """Test that _scan_workspace_for_generated_files only scans the session's dir."""

    def test_scans_only_session_dir(self, tmp_path: Path) -> None:
        """Files in other session dirs should not be picked up."""
        workspace = tmp_path
        sid_a = "sess_aaa111bbb222"
        sid_b = "sess_ccc333ddd444"

        # Create dirs
        (workspace / "outputs" / sid_a).mkdir(parents=True)
        (workspace / "outputs" / sid_b).mkdir(parents=True)

        # File in session A's dir
        file_a = workspace / "outputs" / sid_a / "report.pdf"
        file_a.write_text("session a file")

        # File in session B's dir (should NOT be picked up by session A's scan)
        file_b = workspace / "outputs" / sid_b / "other.pdf"
        file_b.write_text("session b file")

        # Scan for session A
        result = main_server._scan_workspace_for_generated_files(
            workspace, "alice", sid_a
        )
        assert len(result) == 1
        assert result[0]["filename"] == "report.pdf"  # basename only, no path
        assert "__" in result[0]["stored_name"]  # UUID suffix present
        assert result[0]["stored_name"].startswith("report__")  # original stem preserved
        assert result[0]["stored_name"].endswith(".pdf")  # extension preserved

        # Scan for session B
        result_b = main_server._scan_workspace_for_generated_files(
            workspace, "alice", sid_b
        )
        assert len(result_b) == 1
        assert result_b[0]["filename"] == "other.pdf"
        assert "__" in result_b[0]["stored_name"]

    def test_returns_empty_for_nonexistent_session_dir(self, tmp_path: Path) -> None:
        """Nonexistent session dir should return empty list."""
        workspace = tmp_path
        result = main_server._scan_workspace_for_generated_files(
            workspace, "alice", "sess_nonexistent123"
        )
        assert result == []

    def test_file_renamed_on_disk_with_uuid(self, tmp_path: Path) -> None:
        """Scan should rename the file on disk with UUID suffix."""
        workspace = tmp_path
        sid = "sess_aaa111bbb222"
        (workspace / "outputs" / sid).mkdir(parents=True)

        original = workspace / "outputs" / sid / "report.pdf"
        original.write_text("content")

        result = main_server._scan_workspace_for_generated_files(
            workspace, "alice", sid
        )

        # Original file should no longer exist (was renamed)
        assert not original.exists()

        # Renamed file should exist with UUID suffix
        stored = result[0]["stored_name"]
        assert (workspace / "outputs" / sid / stored).exists()

        # filename should be the original basename, not a path
        assert "/" not in result[0]["filename"]
        assert result[0]["filename"] == "report.pdf"

    def test_already_renamed_file_not_double_wrapped(self, tmp_path: Path) -> None:
        """A file with an existing __{uuid8} suffix should be skipped, not re-renamed."""
        workspace = tmp_path
        sid = "sess_aaa111bbb222"
        (workspace / "outputs" / sid).mkdir(parents=True)

        # Simulate a file already processed in a previous scan
        already_renamed = workspace / "outputs" / sid / "report__9a21ed5e.pdf"
        already_renamed.write_text("old content")

        # And a new file from the current task
        new_file = workspace / "outputs" / sid / "report.pdf"
        new_file.write_text("new content")

        result = main_server._scan_workspace_for_generated_files(
            workspace, "alice", sid
        )

        # Only the new file should be picked up
        assert len(result) == 1
        assert result[0]["filename"] == "report.pdf"
        # The already-renamed file should still exist with its original name
        assert already_renamed.exists()
        # And its name should not have a double UUID
        assert already_renamed.name == "report__9a21ed5e.pdf"

    def test_generate_stored_name_strips_existing_uuid(self) -> None:
        """_generate_stored_name should strip existing UUID suffix before adding new one."""
        import re

        result = main_server._generate_stored_name("report__9a21ed5e.pdf")
        # Should have exactly one UUID suffix, not two
        assert re.match(r"^report__[0-9a-f]{8}\.pdf$", result), (
            f"Expected 'report__{{uuid8}}.pdf', got '{result}'"
        )


# ── Write tool path redirection ─────────────────────────────────────


class TestWriteToolPathRedirection:
    """Test that Write tool paths are redirected to outputs/{session_id}/."""

    def test_outputs_file_redirected_to_session_dir(self) -> None:
        """Agent writing to outputs/report.pdf should be redirected to outputs/{sid}/report.pdf."""
        sid = "sess_aaa111bbb222"
        original = "outputs/report.pdf"
        redirected = main_server._normalize_write_path(original, sid)
        assert redirected == f"outputs/{sid}/report.pdf"

    def test_root_file_redirected_to_session_dir(self) -> None:
        """Agent writing to report.pdf should be redirected to outputs/{sid}/report.pdf."""
        sid = "sess_aaa111bbb222"
        original = "report.pdf"
        redirected = main_server._normalize_write_path(original, sid)
        assert redirected == f"outputs/{sid}/report.pdf"

    def test_already_correct_path_unchanged(self) -> None:
        """Agent writing to outputs/{sid}/report.pdf should not be modified."""
        sid = "sess_aaa111bbb222"
        original = f"outputs/{sid}/report.pdf"
        redirected = main_server._normalize_write_path(original, sid)
        assert redirected == original

    def test_subdir_outputs_redirected(self) -> None:
        """Agent writing to outputs/reports/report.pdf should redirect to outputs/{sid}/reports/report.pdf."""
        sid = "sess_aaa111bbb222"
        original = "outputs/reports/report.pdf"
        redirected = main_server._normalize_write_path(original, sid)
        assert redirected == f"outputs/{sid}/reports/report.pdf"


# ── Prompt session path guidance ────────────────────────────────────


class TestPromptSessionPathGuidance:
    """Test that agent prompts include session output path guidance."""

    def test_first_message_includes_session_output_path(self) -> None:
        """_format_first_message_prompt should mention the session output dir."""
        sid = "sess_aaa111bbb222"
        result = main_server._format_first_message_prompt(
            user_message="Write a report",
            attached_files=None,
            language=None,
            session_id=sid,
        )
        assert f"outputs/{sid}" in result

    def test_first_message_with_attachments_still_includes_path(self) -> None:
        """Attachments should not replace session path guidance."""
        sid = "sess_aaa111bbb222"
        result = main_server._format_first_message_prompt(
            user_message="Analyze this",
            attached_files=["uploads/data.csv"],
            language=None,
            session_id=sid,
        )
        assert f"outputs/{sid}" in result
        assert "uploads/data.csv" in result


# ── Download endpoint compatibility ─────────────────────────────────


class TestDownloadEndpointCompatibility:
    """Test that download endpoint works with both path formats."""

    def test_download_session_path(self, tmp_path: Path) -> None:
        """New session paths should be downloadable without changes."""
        user = "alice"
        sid = "sess_aaa111bbb222"
        workspace = tmp_path / "users" / user / "workspace"
        session_dir = workspace / "outputs" / sid
        session_dir.mkdir(parents=True)
        test_file = session_dir / "report.pdf"
        test_file.write_text("test content")

        main_server.DATA_ROOT = tmp_path

        client = TestClient(main_server.app)
        resp = client.get(f"/api/users/{user}/download/outputs/{sid}/report.pdf")
        assert resp.status_code == 200
        assert resp.content == b"test content"

    def test_download_old_session_path(self, tmp_path: Path) -> None:
        """Old session paths (outputs/filename) should still be downloadable."""
        user = "alice"
        workspace = tmp_path / "users" / user / "workspace"
        outputs = workspace / "outputs"
        outputs.mkdir(parents=True)
        test_file = outputs / "legacy.pdf"
        test_file.write_text("legacy content")

        main_server.DATA_ROOT = tmp_path

        client = TestClient(main_server.app)
        resp = client.get(f"/api/users/{user}/download/outputs/legacy.pdf")
        assert resp.status_code == 200
        assert resp.content == b"legacy content"

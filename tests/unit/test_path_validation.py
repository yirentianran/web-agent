"""Tests for path validation and file generation rules.

These tests verify that:
1. File paths are validated to be within the workspace
2. Bash commands that write outside workspace are caught
3. System prompt includes the correct workspace path
"""

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


# ── is_path_within_workspace ───────────────────────────────────────

class TestIsPathWithinWorkspace:
    """Test that Write tool file paths are validated against the workspace."""

    def test_relative_path_is_within_workspace(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        assert main_server.is_path_within_workspace("outputs/file.txt", workspace) is True
        assert main_server.is_path_within_workspace("hello.docx", workspace) is True

    def test_absolute_path_inside_workspace(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        assert main_server.is_path_within_workspace(str(workspace / "outputs/file.txt"), workspace) is True

    def test_absolute_path_outside_workspace(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        assert main_server.is_path_within_workspace("/Users/mac/outputs/content.txt", workspace) is False
        assert main_server.is_path_within_workspace("/tmp/file.txt", workspace) is False
        assert main_server.is_path_within_workspace("/home/user/file.docx", workspace) is False

    def test_traversal_path_blocked(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        result = main_server.is_path_within_workspace("../../../etc/passwd", workspace)
        assert result is False


# ── check_bash_command_for_external_writes ─────────────────────────

class TestCheckBashCommandForExternalWrites:
    """Test that Bash commands writing outside workspace are caught."""

    def test_cp_command_to_users_dir_blocked(self) -> None:
        result = main_server.check_bash_command_for_external_writes(
            "cp template.docx /Users/mac/outputs/content.txt",
            Path("/workspace"),
        )
        assert result is not None

    def test_python_script_writing_to_tmp_blocked(self) -> None:
        result = main_server.check_bash_command_for_external_writes(
            "python generate.py > /tmp/output.txt",
            Path("/workspace"),
        )
        assert result is not None

    def test_python_script_writing_to_workspace_allowed(self) -> None:
        result = main_server.check_bash_command_for_external_writes(
            "python generate.py > outputs/content.txt",
            Path("/workspace"),
        )
        assert result is None

    def test_simple_ls_allowed(self) -> None:
        result = main_server.check_bash_command_for_external_writes(
            "ls -la",
            Path("/workspace"),
        )
        assert result is None

    def test_redirect_to_users_mac_outputs_blocked(self) -> None:
        result = main_server.check_bash_command_for_external_writes(
            "python gen.py && cp result.docx /Users/mac/outputs/content.txt",
            Path("/workspace"),
        )
        assert result is not None


# ── System prompt file generation rules ────────────────────────────

class TestSystemPromptFileRules:
    """Test that the system prompt includes workspace-aware file generation rules."""

    def test_prompt_includes_workspace_path(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        prompt = main_server.build_file_generation_rules_prompt(workspace)
        assert str(workspace) in prompt

    def test_prompt_blocks_absolute_paths(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        prompt = main_server.build_file_generation_rules_prompt(workspace)
        assert "/Users/" in prompt or "absolute" in prompt.lower()

    def test_prompt_includes_correct_examples(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        prompt = main_server.build_file_generation_rules_prompt(workspace)
        assert "outputs/" in prompt

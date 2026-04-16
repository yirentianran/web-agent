"""Tests for post-task file filtering.

Verify that infrastructure files (.log, .pyc, etc.) are never included
in the generated files list, while user-facing result files are.
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


class TestShouldIncludeGeneratedFile:
    """Test that infrastructure files are never offered as downloadable results."""

    def test_server_log_excluded(self) -> None:
        """server.log should be excluded from generated files."""
        assert main_server.should_include_generated_file("server.log") is False

    def test_log_extension_excluded(self) -> None:
        """Any .log file should be excluded."""
        assert main_server.should_include_generated_file("output.log") is False

    def test_pyc_extension_excluded(self) -> None:
        """Any .pyc file should be excluded."""
        assert main_server.should_include_generated_file("__pycache__.pyc") is False

    def test_lock_extension_excluded(self) -> None:
        """Any .lock file should be excluded."""
        assert main_server.should_include_generated_file("pip.lock") is False

    def test_docx_included(self) -> None:
        """Document files should be included."""
        assert main_server.should_include_generated_file("report.docx") is True

    def test_xlsx_included(self) -> None:
        """Spreadsheet files should be included."""
        assert main_server.should_include_generated_file("data.xlsx") is True

    def test_pdf_included(self) -> None:
        """PDF files should be included."""
        assert main_server.should_include_generated_file("report.pdf") is True

    # ── Invalid filename rejection ─────────────────────────────────

    def test_literal_null_excluded(self) -> None:
        """The literal string 'null' should be excluded (not a real filename)."""
        assert main_server.should_include_generated_file("null") is False

    def test_literal_undefined_excluded(self) -> None:
        """The literal string 'undefined' should be excluded."""
        assert main_server.should_include_generated_file("undefined") is False

    def test_case_insensitive_null_excluded(self) -> None:
        """'NULL' in any case should be excluded."""
        assert main_server.should_include_generated_file("NULL") is False

    def test_case_insensitive_undefined_excluded(self) -> None:
        """'UNDEFINED' in any case should be excluded."""
        assert main_server.should_include_generated_file("Undefined") is False

    def test_no_extension_excluded(self) -> None:
        """Files without extensions should be excluded."""
        assert main_server.should_include_generated_file("output") is False

    def test_null_with_extension_still_excluded(self) -> None:
        """'null.txt' has a valid extension but 'null' name is still rejected."""
        assert main_server.should_include_generated_file("null.txt") is False

    # ── Script / code files excluded from generated results ──────────

    def test_py_script_excluded(self) -> None:
        """Python scripts should not appear in generated file results."""
        assert main_server.should_include_generated_file("extract_bank_data.py") is False

    def test_js_script_excluded(self) -> None:
        """JavaScript files should not appear in generated file results."""
        assert main_server.should_include_generated_file("analyze.js") is False

    def test_sh_script_excluded(self) -> None:
        """Shell scripts should not appear in generated file results."""
        assert main_server.should_include_generated_file("run.sh") is False

    def test_json_excluded(self) -> None:
        """JSON data files should not appear in generated file results (internal format)."""
        assert main_server.should_include_generated_file("config.json") is False

    def test_undefined_with_extension_still_excluded(self) -> None:
        """'undefined.json' has a valid extension but 'undefined' name is still rejected."""
        assert main_server.should_include_generated_file("undefined.json") is False

    def test_empty_string_excluded(self) -> None:
        """Empty filename should be excluded."""
        assert main_server.should_include_generated_file("") is False

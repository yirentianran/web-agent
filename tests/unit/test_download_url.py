"""Tests for download URL construction and generated file filtering.

Verify that download URLs always include the correct directory prefix
(e.g., outputs/) and that special characters in filenames are handled safely.
Also verify that infrastructure files (.log, .pyc, etc.) are never offered
as downloadable generated files.
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


class TestBuildDownloadUrl:
    """Test that download URLs include the correct directory prefix."""

    def test_outputs_file_gets_outputs_prefix(self) -> None:
        """Files written to outputs/ should get /api/users/{user}/download/outputs/{filename}."""
        url = main_server.build_download_url("yguo", "outputs/report.docx")
        assert url == "/api/users/yguo/download/outputs/report.docx"

    def test_outputs_file_by_filename_only(self) -> None:
        """Files that are known to be in outputs/ should get the prefix even with just filename."""
        url = main_server.build_download_url("yguo", "report.docx", directory="outputs")
        assert url == "/api/users/yguo/download/outputs/report.docx"

    def test_root_file_no_prefix(self) -> None:
        """Files in workspace root should not get outputs/ prefix."""
        url = main_server.build_download_url("yguo", "script.py")
        assert url == "/api/users/yguo/download/script.py"

    def test_uploads_file_gets_uploads_prefix(self) -> None:
        """Uploaded files should get uploads/ prefix."""
        url = main_server.build_download_url("yguo", "uploads/data.csv")
        assert url == "/api/users/yguo/download/uploads/data.csv"

    def test_special_chars_in_filename(self) -> None:
        """Filenames with special characters should work in the URL path."""
        url = main_server.build_download_url("yguo", "outputs/d'd.txt")
        assert url == "/api/users/yguo/download/outputs/d'd.txt"

    def test_absolute_path_extracts_filename(self) -> None:
        """Absolute paths should extract just the filename, not embed the path in URL."""
        url = main_server.build_download_url("yguo", "/Users/mac/Documents/Projects/web-agent/data/users/yguo/workspace/ddd2.txt")
        assert url == "/api/users/yguo/download/outputs/ddd2.txt"
        assert "/Users/mac" not in url

    def test_absolute_path_to_tmp(self) -> None:
        """Absolute paths to /tmp should also extract just the filename."""
        url = main_server.build_download_url("yguo", "/tmp/result.csv")
        assert url == "/api/users/yguo/download/outputs/result.csv"
        assert "/tmp" not in url

    def test_deeply_nested_absolute_path(self) -> None:
        """Deeply nested absolute paths should not produce double-slashes or embedded paths."""
        url = main_server.build_download_url("yguo", "/a/b/c/d/file.txt")
        assert url == "/api/users/yguo/download/outputs/file.txt"
        assert "//" not in url


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

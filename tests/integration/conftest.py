"""Shared fixtures for integration tests.

Mocks ``claude_agent_sdk`` before main_server imports it,
provides a temporary DATA_ROOT, and a TestClient instance.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

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
    main_server.buffer = main_server.MessageBuffer(base_dir=tmp_path / ".msg-buffer")
    main_server.active_tasks.clear()
    main_server.pending_answers.clear()
    (tmp_path / "users").mkdir(exist_ok=True)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(main_server.app)


@pytest.fixture()
def mock_sdk() -> MagicMock:
    """Return the mocked SDK module for configuring responses."""
    return _mock_sdk

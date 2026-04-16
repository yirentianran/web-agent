"""Unit tests for container_manager — Docker lifecycle management."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import docker.errors
import pytest

import src.container_manager as cm


@pytest.fixture(autouse=True)
def reset_client_cache() -> None:
    """Reset the module-level _client cache between tests."""
    cm._client = None
    yield
    cm._client = None


@pytest.fixture()
def tmp_data_root(tmp_path: Path) -> Path:
    """Provide a temporary DATA_ROOT for per-user path tests."""
    original = cm.DATA_ROOT
    cm.DATA_ROOT = tmp_path
    yield tmp_path
    cm.DATA_ROOT = original


# ── user paths ────────────────────────────────────────────────────


class TestUserPaths:
    def test_user_data_dir(self, tmp_data_root: Path) -> None:
        result = cm.user_data_dir("alice")
        assert result == tmp_data_root / "users" / "alice"

    def test_ensure_user_dirs_creates_workspace(self, tmp_data_root: Path) -> None:
        cm.ensure_user_dirs("alice")
        workspace = tmp_data_root / "users" / "alice" / "workspace" / "uploads"
        assert workspace.exists()
        reports = tmp_data_root / "users" / "alice" / "workspace" / "reports"
        assert reports.exists()

    def test_ensure_user_dirs_idempotent(self, tmp_data_root: Path) -> None:
        cm.ensure_user_dirs("alice")
        cm.ensure_user_dirs("alice")  # should not raise
        assert (tmp_data_root / "users" / "alice" / "workspace").exists()


# ── volume configuration ──────────────────────────────────────────


class TestUserVolumes:
    def test_volume_bindings(self, tmp_data_root: Path) -> None:
        volumes = cm.get_user_volumes("alice")
        # Workspace should be rw
        assert str(tmp_data_root / "users" / "alice" / "workspace") in volumes
        assert volumes[str(tmp_data_root / "users" / "alice" / "workspace")]["mode"] == "rw"
        # Shared skills should be ro
        assert str(tmp_data_root / "shared-skills") in volumes
        assert volumes[str(tmp_data_root / "shared-skills")]["mode"] == "ro"
        # Claude data should be rw
        assert str(tmp_data_root / "users" / "alice" / "claude-data") in volumes
        assert volumes[str(tmp_data_root / "users" / "alice" / "claude-data")]["mode"] == "rw"


# ── environment ───────────────────────────────────────────────────


class TestUserEnv:
    def test_env_has_user_id(self, tmp_data_root: Path) -> None:
        env = cm.get_user_env("alice")
        assert env["USER_ID"] == "alice"

    def test_env_falls_back_to_shared_api_key(self, tmp_data_root: Path) -> None:
        """When user-specific key is absent, falls back to ANTHROPIC_API_KEY."""
        with patch("src.container_manager.os.getenv") as mock_getenv:
            # Simulate: ANTHROPIC_API_KEY_ALICE not set → returns sentinel
            # Then ANTHROPIC_API_KEY is checked → returns "shared-key"
            def getenv_side_effect(key: str, default: str = "") -> str:
                if key == "ANTHROPIC_API_KEY_ALICE":
                    return default  # returns the fallback value
                if key == "ANTHROPIC_API_KEY":
                    return "shared-key"
                return default

            mock_getenv.side_effect = getenv_side_effect
            result_env = cm.get_user_env("alice")
            assert result_env["ANTHROPIC_API_KEY"] == "shared-key"

    def test_env_mcp_config_injected(self, tmp_data_root: Path) -> None:
        mcp = {"mcpServers": {"test": {"type": "http", "url": "http://test"}}}
        env = cm.get_user_env("alice", mcp_config=mcp)
        assert "MCP_CONFIG_JSON" in env
        parsed = json.loads(env["MCP_CONFIG_JSON"])
        assert parsed == mcp

    def test_settings_json_written(self, tmp_data_root: Path) -> None:
        cm.get_user_env("alice")
        settings_path = cm.user_data_dir("alice") / "claude-data" / "settings.json"
        assert settings_path.exists()
        data = json.loads(settings_path.read_text())
        assert "allowedTools" in data
        assert "permissionMode" in data


# ── container lifecycle ───────────────────────────────────────────


class TestContainerLifecycle:
    @patch("src.container_manager.get_client")
    def test_ensure_container_creates_new(self, mock_get_client: MagicMock, tmp_data_root: Path) -> None:
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.attrs = {
            "NetworkSettings": {
                "Ports": {"8000/tcp": [{"HostPort": "55555"}]}
            }
        }

        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")
        mock_client.containers.run.return_value = mock_container
        mock_get_client.return_value = mock_client

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False):
            url = cm.ensure_container("alice")

        mock_client.containers.run.assert_called_once()
        assert url == "http://localhost:55555"

    @patch("src.container_manager.get_client")
    def test_ensure_container_unpauses(self, mock_get_client: MagicMock, tmp_data_root: Path) -> None:
        mock_container = MagicMock()
        mock_container.status = "paused"
        mock_container.attrs = {
            "NetworkSettings": {
                "Ports": {"8000/tcp": [{"HostPort": "44444"}]}
            }
        }

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_get_client.return_value = mock_client

        cm.ensure_container("alice")
        mock_container.unpause.assert_called_once()

    @patch("src.container_manager.get_client")
    def test_ensure_container_restarts_exited(self, mock_get_client: MagicMock, tmp_data_root: Path) -> None:
        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_container.attrs = {
            "NetworkSettings": {
                "Ports": {"8000/tcp": [{"HostPort": "33333"}]}
            }
        }

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_get_client.return_value = mock_client

        cm.ensure_container("alice")
        mock_container.start.assert_called_once()

    @patch("src.container_manager.get_client")
    def test_ensure_container_returns_fallback_url(self, mock_get_client: MagicMock, tmp_data_root: Path) -> None:
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.attrs = {
            "NetworkSettings": {
                "Ports": {"8000/tcp": None}
            }
        }

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_get_client.return_value = mock_client

        url = cm.ensure_container("alice")
        assert url == "http://web-agent-alice:8000"

    @patch("src.container_manager.get_client")
    def test_pause_container(self, mock_get_client: MagicMock, tmp_data_root: Path) -> None:
        mock_container = MagicMock()
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_get_client.return_value = mock_client

        cm.pause_container("alice")
        mock_container.pause.assert_called_once()

    @patch("src.container_manager.get_client")
    def test_pause_container_missing(self, mock_get_client: MagicMock, tmp_data_root: Path) -> None:
        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")
        mock_get_client.return_value = mock_client

        cm.pause_container("alice")  # should not raise

    @patch("src.container_manager.get_client")
    def test_destroy_container(self, mock_get_client: MagicMock, tmp_data_root: Path) -> None:
        mock_container = MagicMock()
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_get_client.return_value = mock_client

        cm.destroy_container("alice")
        mock_container.remove.assert_called_once_with(force=True)

    @patch("src.container_manager.get_client")
    def test_destroy_container_missing(self, mock_get_client: MagicMock, tmp_data_root: Path) -> None:
        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")
        mock_get_client.return_value = mock_client

        cm.destroy_container("alice")  # should not raise

    @patch("src.container_manager.get_client")
    def test_list_active_containers(self, mock_get_client: MagicMock, tmp_data_root: Path) -> None:
        c1 = MagicMock()
        c1.name = "web-agent-alice"
        c1.status = "running"
        c2 = MagicMock()
        c2.name = "web-agent-bob"
        c2.status = "running"

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [c1, c2]
        mock_get_client.return_value = mock_client

        result = cm.list_active_containers()
        assert len(result) == 2
        assert result[0]["name"] == "alice"
        assert result[1]["name"] == "bob"


# ── container name ────────────────────────────────────────────────


class TestContainerName:
    def test_container_name_format(self) -> None:
        assert cm.container_name("alice") == "web-agent-alice"
        assert cm.container_name("bob") == "web-agent-bob"

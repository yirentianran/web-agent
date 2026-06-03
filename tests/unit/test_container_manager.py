"""Unit tests for container_manager — Docker lifecycle management."""

from __future__ import annotations

import json
import os
import time
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


@pytest.fixture(autouse=True)
def reset_last_activity() -> None:
    """Reset the module-level _last_activity dict between tests."""
    cm._last_activity.clear()
    yield
    cm._last_activity.clear()


@pytest.fixture()
def tmp_data_root(tmp_path: Path) -> Path:
    """Provide a temporary DATA_ROOT and HOST_DATA_ROOT for tests."""
    original_data = cm.DATA_ROOT
    original_host = cm.HOST_DATA_ROOT
    cm.DATA_ROOT = tmp_path
    cm.HOST_DATA_ROOT = tmp_path
    yield tmp_path
    cm.DATA_ROOT = original_data
    cm.HOST_DATA_ROOT = original_host


# ── user paths ────────────────────────────────────────────────────


class TestUserPaths:
    def test_user_data_dir(self, tmp_data_root: Path) -> None:
        result = cm.user_data_dir("alice")
        assert result == tmp_data_root / "users" / "alice"

    def test_container_user_dir(self, tmp_data_root: Path) -> None:
        result = cm.container_user_dir("alice")
        assert result == tmp_data_root / "users" / "alice"

    def test_container_workspace_dir(self, tmp_data_root: Path) -> None:
        result = cm.container_workspace_dir("alice")
        assert result == tmp_data_root / "users" / "alice" / "workspace"

    def test_ensure_user_dirs_creates_workspace(self, tmp_data_root: Path) -> None:
        cm.ensure_user_dirs("alice")
        workspace = tmp_data_root / "users" / "alice" / "workspace" / "uploads"
        assert workspace.exists()

    def test_ensure_user_dirs_idempotent(self, tmp_data_root: Path) -> None:
        cm.ensure_user_dirs("alice")
        cm.ensure_user_dirs("alice")  # should not raise
        assert (tmp_data_root / "users" / "alice" / "workspace").exists()


# ── volume configuration ──────────────────────────────────────────


class TestUserVolumes:
    def test_volume_bindings(self, tmp_data_root: Path) -> None:
        volumes = cm.get_user_volumes("alice")

        workspace_key = str(tmp_data_root / "users" / "alice" / "workspace")
        claude_key = str(tmp_data_root / "users" / "alice" / ".claude")
        shared_key = str(tmp_data_root / "shared-skills")

        # Workspace — rw, bind target matches source
        assert workspace_key in volumes
        assert volumes[workspace_key]["bind"] == workspace_key
        assert volumes[workspace_key]["mode"] == "rw"

        # Claude data — rw, bind target matches source
        assert claude_key in volumes
        assert volumes[claude_key]["bind"] == claude_key
        assert volumes[claude_key]["mode"] == "rw"

        # Shared skills — ro, bind target matches source
        assert shared_key in volumes
        assert volumes[shared_key]["bind"] == shared_key
        assert volumes[shared_key]["mode"] == "ro"


# ── environment ───────────────────────────────────────────────────


class TestUserEnv:
    def test_env_has_user_id(self, tmp_data_root: Path) -> None:
        env = cm.get_user_env("alice")
        assert env["USER_ID"] == "alice"

    def test_env_has_host_matching_paths(self, tmp_data_root: Path) -> None:
        env = cm.get_user_env("alice")
        expected_workspace = str(tmp_data_root / "users" / "alice" / "workspace")
        expected_home = str(tmp_data_root / "users" / "alice")
        assert env["WORKSPACE"] == expected_workspace
        assert env["HOME"] == expected_home

    def test_env_skills_dirs_host_matching(self, tmp_data_root: Path) -> None:
        env = cm.get_user_env("alice")
        skills = env["CLAUDE_SKILLS_DIRS"].split(",")
        assert str(tmp_data_root / "shared-skills") in skills
        assert str(tmp_data_root / "users" / "alice" / "workspace" / ".claude" / "skills") in skills

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

# ── container lifecycle ───────────────────────────────────────────


class TestContainerLifecycle:
    @patch("src.container_manager.wait_for_container_ready")
    @patch("src.container_manager.get_client")
    def test_ensure_container_creates_new(self, mock_get_client: MagicMock, mock_wait: MagicMock, tmp_data_root: Path) -> None:
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
        assert url == "http://127.0.0.1:55555"

    @patch("src.container_manager.wait_for_container_ready")
    @patch("src.container_manager.get_client")
    def test_ensure_container_unpauses(self, mock_get_client: MagicMock, mock_wait: MagicMock, tmp_data_root: Path) -> None:
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

    @patch("src.container_manager.wait_for_container_ready")
    @patch("src.container_manager.get_client")
    def test_ensure_container_restarts_exited(self, mock_get_client: MagicMock, mock_wait: MagicMock, tmp_data_root: Path) -> None:
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

    @patch("src.container_manager.wait_for_container_ready")
    @patch("src.container_manager.get_client")
    def test_ensure_container_no_port_mapping_raises(self, mock_get_client: MagicMock, mock_wait: MagicMock, tmp_data_root: Path) -> None:
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

        # When port mapping is missing and container never gets one,
        # RuntimeError is raised after the 10s wait loop.
        # time.time() call order:
        #   1. touch_user("alice")
        #   2. deadline = time.time() + 10
        #   3..N. while loop condition (needs value >= deadline to exit)
        times = iter([0.0, 0.0] + [10.0] * 100)
        with patch("time.sleep", return_value=None), \
             patch("time.time", side_effect=lambda: next(times)):
            with pytest.raises(RuntimeError, match="no host port mapping"):
                cm.ensure_container("alice")

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


# ── idle tracking ──────────────────────────────────────────────────


class TestIdleTracking:
    def test_touch_user_records_timestamp(self) -> None:
        before = time.time()
        cm.touch_user("alice")
        after = time.time()
        assert "alice" in cm._last_activity
        assert before <= cm._last_activity["alice"] <= after

    def test_touch_user_overwrites_previous(self) -> None:
        cm._last_activity["alice"] = 100.0
        cm.touch_user("alice")
        assert cm._last_activity["alice"] > 100.0

    @patch("src.container_manager.get_client")
    def test_stop_container(self, mock_get_client: MagicMock) -> None:
        mock_container = MagicMock()
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_get_client.return_value = mock_client

        cm.stop_container("alice")
        mock_container.stop.assert_called_once_with(timeout=30)

    @patch("src.container_manager.get_client")
    def test_stop_container_missing(self, mock_get_client: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")
        mock_get_client.return_value = mock_client

        cm.stop_container("alice")  # should not raise

    @patch("src.container_manager.get_client")
    def test_stop_idle_containers_stops_idle(self, mock_get_client: MagicMock) -> None:
        now = time.time()
        cm._last_activity["alice"] = now - 3600  # 1 hour idle
        cm._last_activity["bob"] = now  # freshly active

        mock_container_a = MagicMock()
        mock_container_a.status = "running"
        mock_container_a.name = "web-agent-alice"
        mock_container_b = MagicMock()
        mock_container_b.status = "running"
        mock_container_b.name = "web-agent-bob"
        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_container_a, mock_container_b]
        mock_get_client.return_value = mock_client

        # Temporarily reduce TTL to ensure alice is detected as idle
        original_ttl = cm.CONTAINER_IDLE_TTL
        cm.CONTAINER_IDLE_TTL = 100  # 100 seconds
        try:
            stopped = cm.stop_idle_containers()
            assert stopped == 1
            mock_container_a.stop.assert_called_once()
            mock_container_b.stop.assert_not_called()
        finally:
            cm.CONTAINER_IDLE_TTL = original_ttl

    @patch("src.container_manager.get_client")
    def test_stop_idle_containers_all_active(self, mock_get_client: MagicMock) -> None:
        now = time.time()
        cm._last_activity["alice"] = now
        cm._last_activity["bob"] = now

        mock_container_a = MagicMock()
        mock_container_a.status = "running"
        mock_container_a.name = "web-agent-alice"
        mock_container_b = MagicMock()
        mock_container_b.status = "running"
        mock_container_b.name = "web-agent-bob"
        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_container_a, mock_container_b]
        mock_get_client.return_value = mock_client

        stopped = cm.stop_idle_containers()
        assert stopped == 0
        mock_container_a.stop.assert_not_called()
        mock_container_b.stop.assert_not_called()

    @patch("src.container_manager.get_client")
    def test_stop_idle_containers_ignores_missing_containers(self, mock_get_client: MagicMock) -> None:
        """Container not found via list() is simply not stopped — no error."""
        cm._last_activity["alice"] = 0.0  # very old, but no matching container

        mock_client = MagicMock()
        mock_client.containers.list.return_value = []  # no running containers
        mock_get_client.return_value = mock_client

        original_ttl = cm.CONTAINER_IDLE_TTL
        cm.CONTAINER_IDLE_TTL = 1
        try:
            stopped = cm.stop_idle_containers()
            assert stopped == 0
        finally:
            cm.CONTAINER_IDLE_TTL = original_ttl

    @patch("src.container_manager.wait_for_container_ready")
    @patch("src.container_manager.get_client")
    def test_ensure_container_calls_touch_user(self, mock_get_client: MagicMock, mock_wait: MagicMock, tmp_data_root: Path) -> None:
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

        assert "alice" not in cm._last_activity
        cm.ensure_container("alice")
        assert "alice" in cm._last_activity

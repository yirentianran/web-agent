"""Unit tests for per-user MCP server management.

Each user can manage their own MCP servers without admin privileges.
Servers are stored per-user in JSON files under the user data directory.
"""

from __future__ import annotations

import json
import sys
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

from fastapi.testclient import TestClient

import main_server
from src.user_mcp_manager import UserMcpManager


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


class TestPerUserMcpListServers:
    def test_list_empty_returns_empty_list(self, client: TestClient) -> None:
        """A user with no MCP servers gets an empty list."""
        resp = client.get("/api/users/alice/mcp-servers")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_user_servers_only(self, client: TestClient) -> None:
        """Users only see their own servers, not others'."""
        # Add server for alice
        client.post(
            "/api/users/alice/mcp-servers",
            json={"name": "alice-fs", "type": "stdio", "command": "ls", "tools": ["list"]},
        )
        # Add server for bob
        client.post(
            "/api/users/bob/mcp-servers",
            json={"name": "bob-fs", "type": "stdio", "command": "ls", "tools": ["list"]},
        )

        resp = client.get("/api/users/alice/mcp-servers")
        assert resp.status_code == 200
        servers = resp.json()
        assert len(servers) == 1
        assert servers[0]["name"] == "alice-fs"


class TestPerUserMcpCreateServer:
    def test_create_stdio_server(self, client: TestClient) -> None:
        """Create a stdio-type MCP server."""
        resp = client.post(
            "/api/users/alice/mcp-servers",
            json={
                "name": "filesystem",
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-fs", "/tmp"],
                "tools": ["read_file", "write_file", "list_dir"],
                "description": "Local file access",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Verify it appears in list
        resp = client.get("/api/users/alice/mcp-servers")
        servers = resp.json()
        assert len(servers) == 1
        assert servers[0]["name"] == "filesystem"
        assert servers[0]["type"] == "stdio"
        assert servers[0]["command"] == "npx"
        assert servers[0]["enabled"] is True

    def test_create_http_server(self, client: TestClient) -> None:
        """Create an http-type MCP server."""
        resp = client.post(
            "/api/users/alice/mcp-servers",
            json={
                "name": "weather-api",
                "type": "http",
                "url": "https://mcp.example.com/weather",
                "tools": ["get_weather"],
                "description": "Weather data",
            },
        )
        assert resp.status_code == 200

        resp = client.get("/api/users/alice/mcp-servers")
        servers = resp.json()
        assert servers[0]["type"] == "http"
        assert servers[0]["url"] == "https://mcp.example.com/weather"

    def test_duplicate_name_rejected(self, client: TestClient) -> None:
        """Cannot create two servers with the same name."""
        client.post(
            "/api/users/alice/mcp-servers",
            json={"name": "fs", "type": "stdio", "command": "ls", "tools": []},
        )
        resp = client.post(
            "/api/users/alice/mcp-servers",
            json={"name": "fs", "type": "stdio", "command": "ls", "tools": []},
        )
        assert resp.status_code == 409

    def test_name_required(self, client: TestClient) -> None:
        """Server name is required."""
        resp = client.post(
            "/api/users/alice/mcp-servers",
            json={"type": "stdio", "command": "ls", "tools": []},
        )
        assert resp.status_code == 422

    def test_tools_required(self, client: TestClient) -> None:
        """Tools list is required."""
        resp = client.post(
            "/api/users/alice/mcp-servers",
            json={"name": "fs", "type": "stdio", "command": "ls"},
        )
        assert resp.status_code == 422

    def test_stdio_requires_command(self, client: TestClient) -> None:
        """Stdio servers require a command."""
        resp = client.post(
            "/api/users/alice/mcp-servers",
            json={"name": "fs", "type": "stdio", "tools": ["read"]},
        )
        assert resp.status_code == 422

    def test_http_requires_url(self, client: TestClient) -> None:
        """Http servers require a URL."""
        resp = client.post(
            "/api/users/alice/mcp-servers",
            json={"name": "api", "type": "http", "tools": ["fetch"]},
        )
        assert resp.status_code == 422


class TestPerUserMcpUpdateServer:
    def test_update_server(self, client: TestClient) -> None:
        """Update server description and tools."""
        client.post(
            "/api/users/alice/mcp-servers",
            json={"name": "fs", "type": "stdio", "command": "ls", "tools": ["read"]},
        )
        resp = client.put(
            "/api/users/alice/mcp-servers/fs",
            json={
                "name": "fs",
                "type": "stdio",
                "command": "ls",
                "args": ["-la"],
                "tools": ["read", "write"],
                "description": "Updated",
            },
        )
        assert resp.status_code == 200

        resp = client.get("/api/users/alice/mcp-servers")
        server = resp.json()[0]
        assert server["description"] == "Updated"
        assert server["tools"] == ["read", "write"]

    def test_update_nonexistent_server(self, client: TestClient) -> None:
        """Updating a server that doesn't exist returns 404."""
        resp = client.put(
            "/api/users/alice/mcp-servers/nonexistent",
            json={"name": "nonexistent", "type": "stdio", "command": "ls", "tools": []},
        )
        assert resp.status_code == 404


class TestPerUserMcpDeleteServer:
    def test_delete_server(self, client: TestClient) -> None:
        """Delete an existing server."""
        client.post(
            "/api/users/alice/mcp-servers",
            json={"name": "to-delete", "type": "stdio", "command": "ls", "tools": []},
        )
        resp = client.delete("/api/users/alice/mcp-servers/to-delete")
        assert resp.status_code == 200

        resp = client.get("/api/users/alice/mcp-servers")
        assert len(resp.json()) == 0

    def test_delete_nonexistent_server(self, client: TestClient) -> None:
        """Deleting a server that doesn't exist returns 404."""
        resp = client.delete("/api/users/alice/mcp-servers/nonexistent")
        assert resp.status_code == 404


class TestPerUserMcpToggleServer:
    def test_disable_server(self, client: TestClient) -> None:
        """Toggle a server off."""
        client.post(
            "/api/users/alice/mcp-servers",
            json={"name": "fs", "type": "stdio", "command": "ls", "tools": []},
        )
        resp = client.patch("/api/users/alice/mcp-servers/fs/toggle?enabled=false")
        assert resp.status_code == 200

        resp = client.get("/api/users/alice/mcp-servers")
        assert resp.json()[0]["enabled"] is False

    def test_enable_server(self, client: TestClient) -> None:
        """Toggle a server back on."""
        client.post(
            "/api/users/alice/mcp-servers",
            json={"name": "fs", "type": "stdio", "command": "ls", "tools": [], "enabled": False},
        )
        resp = client.patch("/api/users/alice/mcp-servers/fs/toggle?enabled=true")
        assert resp.status_code == 200

        resp = client.get("/api/users/alice/mcp-servers")
        assert resp.json()[0]["enabled"] is True


class TestPerUserMcpPersistence:
    def test_servers_persist_across_requests(self, client: TestClient) -> None:
        """Servers are persisted in the user data directory."""
        client.post(
            "/api/users/alice/mcp-servers",
            json={"name": "persistent", "type": "stdio", "command": "ls", "tools": ["read"]},
        )
        # Read directly from the file
        mcp_file = main_server.user_data_dir("alice") / "mcp-servers.json"
        assert mcp_file.exists()
        data = json.loads(mcp_file.read_text())
        assert "persistent" in data

    def test_servers_load_from_existing_file(self, client: TestClient) -> None:
        """Servers are loaded from an existing file on first list."""
        mcp_file = main_server.user_data_dir("bob") / "mcp-servers.json"
        mcp_file.write_text(json.dumps({
            "preloaded": {
                "name": "preloaded",
                "type": "stdio",
                "command": "echo",
                "tools": ["greet"],
                "description": "Pre-existing",
                "enabled": True,
            }
        }))

        resp = client.get("/api/users/bob/mcp-servers")
        assert resp.status_code == 200
        servers = resp.json()
        assert len(servers) == 1
        assert servers[0]["name"] == "preloaded"


class TestUserMcpManagerGetActiveConfig:
    """Test UserMcpManager.get_active_config() produces correct SDK config."""

    def test_enabled_stdio_server_included(self, tmp_path: Path) -> None:
        mgr = UserMcpManager("alice", tmp_path)
        mgr.create_server({
            "name": "fs", "type": "stdio", "command": "npx",
            "args": ["-y", "mcp-fs"], "tools": ["read", "write"],
        })
        config = mgr.get_active_config()
        assert "mcpServers" in config
        assert "fs" in config["mcpServers"]
        assert config["mcpServers"]["fs"]["command"] == "npx"
        assert config["mcpServers"]["fs"]["args"] == ["-y", "mcp-fs"]

    def test_disabled_server_excluded(self, tmp_path: Path) -> None:
        mgr = UserMcpManager("alice", tmp_path)
        mgr.create_server({
            "name": "disabled", "type": "stdio", "command": "echo",
            "tools": [], "enabled": False,
        })
        config = mgr.get_active_config()
        assert "disabled" not in config["mcpServers"]

    def test_http_server_uses_url(self, tmp_path: Path) -> None:
        mgr = UserMcpManager("alice", tmp_path)
        mgr.create_server({
            "name": "weather", "type": "http", "url": "https://mcp.example.com",
            "tools": ["get_weather"],
        })
        config = mgr.get_active_config()
        assert "weather" in config["mcpServers"]
        assert config["mcpServers"]["weather"]["url"] == "https://mcp.example.com"

    def test_allowed_tools_format(self, tmp_path: Path) -> None:
        mgr = UserMcpManager("alice", tmp_path)
        mgr.create_server({
            "name": "fs", "type": "stdio", "command": "ls",
            "tools": ["read_file", "write_file"],
        })
        config = mgr.get_active_config()
        assert "mcp__fs__read_file" in config["allowed_tools"]
        assert "mcp__fs__write_file" in config["allowed_tools"]

    def test_empty_when_no_servers(self, tmp_path: Path) -> None:
        mgr = UserMcpManager("alice", tmp_path)
        config = mgr.get_active_config()
        assert config == {"mcpServers": {}, "allowed_tools": []}

    def test_env_included_when_present(self, tmp_path: Path) -> None:
        mgr = UserMcpManager("alice", tmp_path)
        mgr.create_server({
            "name": "db", "type": "stdio", "command": "python",
            "args": [], "env": {"DB_HOST": "localhost"},
            "tools": ["query"],
        })
        config = mgr.get_active_config()
        assert config["mcpServers"]["db"]["env"] == {"DB_HOST": "localhost"}

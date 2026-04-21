"""Unit tests for MCP server SQLite store.

Tests the MCPServerStore CRUD operations against a temporary SQLite database.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Mock claude_agent_sdk before any imports that might touch it
_mock_sdk = __import__("unittest.mock").mock.MagicMock()
_mock_sdk.ClaudeSDKClient = __import__("unittest.mock").mock.MagicMock()
_mock_sdk.types = __import__("unittest.mock").mock.MagicMock()
sys.modules.setdefault("claude_agent_sdk", _mock_sdk)
sys.modules.setdefault("claude_agent_sdk.types", _mock_sdk.types)

from src.database import Database
from src.mcp_store import MCPServerStore


@pytest.fixture()
async def db(tmp_path: Path) -> Database:
    """Create a fresh SQLite database with all tables."""
    database = Database(db_path=tmp_path / "test.db")
    await database.init()
    yield database
    await database.close()


@pytest.fixture()
async def store(db: Database) -> MCPServerStore:
    """Create an MCPServerStore instance."""
    return MCPServerStore(db=db)


def _sample_server(name: str = "test-server") -> dict:
    return {
        "name": name,
        "type": "stdio",
        "command": "echo",
        "args": ["hello"],
        "url": None,
        "env": {"API_KEY": "test123"},
        "tools": ["greet", "farewell"],
        "description": "A test MCP server",
        "enabled": True,
        "access": "all",
    }


# ── Create ───────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestMCPServerCreate:
    async def test_create_server_returns_record(self, store: MCPServerStore) -> None:
        server = _sample_server()
        result = await store.create(server)
        assert result["name"] == "test-server"
        assert result["type"] == "stdio"
        assert result["command"] == "echo"
        assert result["enabled"] is True

    async def test_create_server_stores_json_fields(
        self, store: MCPServerStore
    ) -> None:
        server = _sample_server()
        result = await store.create(server)
        assert result["args"] == ["hello"]
        assert result["env"]["API_KEY"] == "test123"
        assert result["tools"] == ["greet", "farewell"]

    async def test_create_duplicate_name_raises(self, store: MCPServerStore) -> None:
        server = _sample_server("dup-server")
        await store.create(server)
        with pytest.raises(ValueError, match="already exists"):
            await store.create(server)

    async def test_create_http_server(self, store: MCPServerStore) -> None:
        server = {
            "name": "http-server",
            "type": "http",
            "url": "http://localhost:8080/mcp",
            "tools": ["query"],
        }
        result = await store.create(server)
        assert result["type"] == "http"
        assert result["url"] == "http://localhost:8080/mcp"


# ── List ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestMCPServerList:
    async def test_list_empty(self, store: MCPServerStore) -> None:
        servers = await store.list_all()
        assert servers == []

    async def test_list_single_server(self, store: MCPServerStore) -> None:
        await store.create(_sample_server())
        servers = await store.list_all()
        assert len(servers) == 1
        assert servers[0]["name"] == "test-server"

    async def test_list_multiple_servers(self, store: MCPServerStore) -> None:
        await store.create(_sample_server("alpha"))
        await store.create(_sample_server("beta"))
        await store.create(_sample_server("gamma"))
        servers = await store.list_all()
        assert len(servers) == 3
        names = {s["name"] for s in servers}
        assert names == {"alpha", "beta", "gamma"}


# ── Get by name ──────────────────────────────────────────────────


@pytest.mark.asyncio
class TestMCPServerGetByName:
    async def test_get_existing_server(self, store: MCPServerStore) -> None:
        await store.create(_sample_server("my-server"))
        result = await store.get_by_name("my-server")
        assert result is not None
        assert result["name"] == "my-server"

    async def test_get_nonexistent_server_returns_none(
        self, store: MCPServerStore
    ) -> None:
        result = await store.get_by_name("does-not-exist")
        assert result is None


# ── Update ───────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestMCPServerUpdate:
    async def test_update_existing_server(self, store: MCPServerStore) -> None:
        await store.create(_sample_server("update-me"))
        updated = await store.update(
            "update-me",
            {"description": "Updated description", "enabled": False},
        )
        assert updated is not None
        assert updated["description"] == "Updated description"
        assert updated["enabled"] is False

    async def test_update_nonexistent_returns_none(self, store: MCPServerStore) -> None:
        result = await store.update("ghost", {"description": "nope"})
        assert result is None

    async def test_update_with_name_change(self, store: MCPServerStore) -> None:
        """When name changes, old entry should be removed and new one created."""
        await store.create(_sample_server("old-name"))
        updated = await store.update(
            "old-name",
            {"name": "new-name", "description": "renamed"},
        )
        assert updated is not None
        assert updated["name"] == "new-name"
        assert updated["description"] == "renamed"

        # Old name should not exist
        old = await store.get_by_name("old-name")
        assert old is None

        # New name should exist
        new = await store.get_by_name("new-name")
        assert new is not None

    async def test_update_preserves_unset_fields(self, store: MCPServerStore) -> None:
        """Fields not in update should retain their original values."""
        await store.create(_sample_server("preserve-me"))
        await store.update("preserve-me", {"description": "new desc"})
        server = await store.get_by_name("preserve-me")
        assert server is not None
        assert server["command"] == "echo"  # unchanged
        assert server["description"] == "new desc"


# ── Delete ───────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestMCPServerDelete:
    async def test_delete_existing_server(self, store: MCPServerStore) -> None:
        await store.create(_sample_server("delete-me"))
        assert await store.delete("delete-me") is True
        assert await store.get_by_name("delete-me") is None

    async def test_delete_nonexistent_returns_false(
        self, store: MCPServerStore
    ) -> None:
        assert await store.delete("ghost") is False


# ── Toggle ───────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestMCPServerToggle:
    async def test_toggle_enabled_to_disabled(self, store: MCPServerStore) -> None:
        await store.create(_sample_server("toggle-me"))
        result = await store.toggle("toggle-me", False)
        assert result is True
        server = await store.get_by_name("toggle-me")
        assert server is not None
        assert server["enabled"] is False

    async def test_toggle_disabled_to_enabled(self, store: MCPServerStore) -> None:
        await store.create(_sample_server("toggle-me"))
        await store.toggle("toggle-me", False)
        result = await store.toggle("toggle-me", True)
        assert result is True
        server = await store.get_by_name("toggle-me")
        assert server is not None
        assert server["enabled"] is True

    async def test_toggle_nonexistent_returns_false(
        self, store: MCPServerStore
    ) -> None:
        assert await store.toggle("ghost", True) is False


# ── Migration from file ──────────────────────────────────────────


@pytest.mark.asyncio
class TestMCPServerMigration:
    async def test_migrate_from_json_file(
        self, db: Database, tmp_path: Path
    ) -> None:
        """Given a mcp-registry.json file, migrate its contents to DB."""
        from src.mcp_store import migrate_from_file

        registry = tmp_path / "mcp-registry.json"
        registry.write_text(json.dumps({
            "mcpServers": {
                "server-a": {
                    "name": "server-a",
                    "type": "stdio",
                    "command": "cmd-a",
                    "args": ["--flag"],
                    "env": {},
                    "tools": ["tool1"],
                    "description": "Server A",
                    "enabled": True,
                    "access": "all",
                },
                "server-b": {
                    "name": "server-b",
                    "type": "http",
                    "url": "http://example.com/mcp",
                    "tools": ["query"],
                    "enabled": False,
                },
            }
        }))

        store = MCPServerStore(db=db)
        migrated = await migrate_from_file(registry, store)
        assert migrated == 2

        servers = await store.list_all()
        names = {s["name"] for s in servers}
        assert names == {"server-a", "server-b"}

    async def test_migrate_nonexistent_file_returns_zero(
        self, db: Database, tmp_path: Path
    ) -> None:
        from src.mcp_store import migrate_from_file

        store = MCPServerStore(db=db)
        nonexistent = tmp_path / "does-not-exist.json"
        count = await migrate_from_file(nonexistent, store)
        assert count == 0

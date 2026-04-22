"""Tests for automatic tool persistence when status check discovers new tools.

Improvement A: /status endpoint should persist discovered tools to DB
when they differ from what's already stored.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Mock claude_agent_sdk before any imports that might touch it
_mock_sdk = __import__("unittest.mock").mock.MagicMock()
_mock_sdk.ClaudeSDKClient = __import__("unittest.mock").mock.MagicMock()
_mock_sdk.types = __import__("unittest.mock").mock.MagicMock()
sys.modules.setdefault("claude_agent_sdk", _mock_sdk)
sys.modules.setdefault("claude_agent_sdk.types", _mock_sdk.types)

from src.database import Database
from src.mcp_store import MCPServerStore

_SAMPLE_SERVER = {
    "name": "test-mcp",
    "type": "stdio",
    "command": "echo",
    "args": [],
    "env": {},
    "tools": [],
    "description": "Test server",
    "enabled": True,
    "access": "all",
}


@pytest.fixture()
async def db(tmp_path: Path) -> Database:
    database = Database(db_path=tmp_path / "test.db")
    await database.init()
    yield database
    await database.close()


@pytest.fixture()
async def store(db: Database) -> MCPServerStore:
    return MCPServerStore(db=db)


@pytest.mark.asyncio
class TestAutoSyncDiscoveredTools:
    """_sync_tools_to_db should persist discovered tools when they differ from DB."""

    async def test_persists_tools_when_db_has_empty_tools(
        self, store: MCPServerStore
    ) -> None:
        from main_server import _sync_tools_to_db

        await store.create(_SAMPLE_SERVER)
        # DB has tools=[] initially
        result = await _sync_tools_to_db("test-mcp", ["parse_documents", "convert_file"], store)
        assert result is True

        # Verify persisted
        updated = await store.get_by_name("test-mcp")
        assert updated is not None
        assert updated["tools"] == ["parse_documents", "convert_file"]

    async def test_persists_tools_when_db_has_different_tools(
        self, store: MCPServerStore
    ) -> None:
        from main_server import _sync_tools_to_db

        server = dict(_SAMPLE_SERVER, tools=["old_tool"])
        await store.create(server)

        result = await _sync_tools_to_db("test-mcp", ["new_tool_a", "new_tool_b"], store)
        assert result is True

        updated = await store.get_by_name("test-mcp")
        assert updated is not None
        assert set(updated["tools"]) == {"new_tool_a", "new_tool_b"}

    async def test_does_nothing_when_tools_already_match(
        self, store: MCPServerStore
    ) -> None:
        from main_server import _sync_tools_to_db

        server = dict(_SAMPLE_SERVER, tools=["tool_a", "tool_b"])
        await store.create(server)

        result = await _sync_tools_to_db("test-mcp", ["tool_a", "tool_b"], store)
        assert result is False  # Nothing changed

        # Verify nothing changed (timestamp could change, but content stays same)
        updated = await store.get_by_name("test-mcp")
        assert set(updated["tools"]) == {"tool_a", "tool_b"}

    async def test_persists_empty_list_when_server_has_no_tools(
        self, store: MCPServerStore
    ) -> None:
        """If discovery returns [], should persist [] (no-op for already-empty)."""
        from main_server import _sync_tools_to_db

        server = dict(_SAMPLE_SERVER, tools=[])
        await store.create(server)

        result = await _sync_tools_to_db("test-mcp", [], store)
        assert result is False  # No change

    async def test_clears_tools_when_discovery_returns_empty_but_db_had_tools(
        self, store: MCPServerStore
    ) -> None:
        from main_server import _sync_tools_to_db

        server = dict(_SAMPLE_SERVER, tools=["stale_tool"])
        await store.create(server)

        result = await _sync_tools_to_db("test-mcp", [], store)
        assert result is True

        updated = await store.get_by_name("test-mcp")
        assert updated is not None
        assert updated["tools"] == []

    async def test_returns_false_for_nonexistent_server(
        self, store: MCPServerStore
    ) -> None:
        from main_server import _sync_tools_to_db

        result = await _sync_tools_to_db("does-not-exist", ["tool"], store)
        assert result is False

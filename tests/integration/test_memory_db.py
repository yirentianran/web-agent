"""Integration tests for MemoryManager with SQLite backend."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Mock claude_agent_sdk before main_server imports it
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

with patch.dict("sys.modules", {"claude_agent_sdk": _mock_sdk, "claude_agent_sdk.types": _mock_sdk.types}):
    from fastapi.testclient import TestClient
    import main_server

from src.database import Database
from src.memory import MemoryManager


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    """Create a temporary database."""
    database = Database(db_path=tmp_path / "test.db")
    await database.init()
    yield database
    await database.close()


@pytest.fixture
def mgr(tmp_path: Path) -> MemoryManager:
    """Create a MemoryManager with temporary data root."""
    return MemoryManager(user_id="alice", data_root=tmp_path)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """Test client with temporary data root."""
    main_server.DATA_ROOT = tmp_path
    main_server.buffer = main_server.MessageBuffer(base_dir=tmp_path / ".msg-buffer")
    main_server.active_tasks.clear()
    main_server.pending_answers.clear()
    (tmp_path / "users").mkdir(exist_ok=True)
    (tmp_path / "users" / "alice").mkdir(exist_ok=True)
    return TestClient(main_server.app)


# ── MemoryManager DB integration ──────────────────────────────────


class TestMemoryManagerDB:
    """MemoryManager reads/writes from SQLite when db is attached."""

    @pytest.mark.asyncio
    async def test_read_returns_from_db(self, db: Database, tmp_path: Path) -> None:
        mgr = MemoryManager(user_id="alice", data_root=tmp_path, db=db)
        # First write to DB
        async with db.connection() as conn:
            await conn.execute(
                "INSERT INTO users (id) VALUES (?)", ("alice",)
            )
            await conn.execute(
                """INSERT INTO user_memory
                   (user_id, preferences, entity_memory, audit_context, file_memory, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("alice", '{"model": "sonnet"}', '{}', '{}', '[]', 1000.0),
            )
            await conn.commit()

        data = mgr.read()
        assert data["preferences"]["model"] == "sonnet"

    @pytest.mark.asyncio
    async def test_update_writes_to_db(self, db: Database, tmp_path: Path) -> None:
        mgr = MemoryManager(user_id="alice", data_root=tmp_path, db=db)
        # Ensure user exists
        async with db.connection() as conn:
            await conn.execute("INSERT OR IGNORE INTO users (id) VALUES (?)", ("alice",))
            await conn.commit()

        updated = mgr.update({"preferences": {"max_budget_usd": 10.0}})
        assert updated["preferences"]["max_budget_usd"] == 10.0

        # Verify in DB
        async with db.connection() as conn:
            cursor = await conn.execute(
                "SELECT preferences FROM user_memory WHERE user_id = ?", ("alice",)
            )
            row = await cursor.fetchone()
            assert row is not None
            import json
            prefs = json.loads(row[0])
            assert prefs["max_budget_usd"] == 10.0

    @pytest.mark.asyncio
    async def test_replace_writes_to_db(self, db: Database, tmp_path: Path) -> None:
        mgr = MemoryManager(user_id="alice", data_root=tmp_path, db=db)
        async with db.connection() as conn:
            await conn.execute("INSERT OR IGNORE INTO users (id) VALUES (?)", ("alice",))
            await conn.commit()

        mgr.replace({
            "preferences": {"model": "opus"},
            "entity_memory": {"company_name": "Test Corp"},
        })

        async with db.connection() as conn:
            cursor = await conn.execute(
                "SELECT preferences, entity_memory FROM user_memory WHERE user_id = ?",
                ("alice",),
            )
            row = await cursor.fetchone()
            import json
            assert row is not None
            prefs = json.loads(row[0])
            entity = json.loads(row[1])
            assert prefs["model"] == "opus"
            assert entity["company_name"] == "Test Corp"

    @pytest.mark.asyncio
    async def test_no_db_falls_back_to_file(self, mgr: MemoryManager) -> None:
        """MemoryManager without db should use file-based storage."""
        mgr.update({"preferences": {"theme": "dark"}})
        data = mgr.read()
        assert data["preferences"]["theme"] == "dark"


# ── Memory API endpoints ──────────────────────────────────────────


class TestMemoryEndpoints:
    """Memory API endpoints work with DB-backed MemoryManager."""

    def test_get_empty_memory(self, client: TestClient) -> None:
        resp = client.get("/api/users/alice/memory")
        assert resp.status_code == 200
        assert resp.json() == {"user_id": "alice"}

    def test_update_and_get_memory(self, client: TestClient) -> None:
        resp = client.put(
            "/api/users/alice/memory",
            json={
                "preferences": {"model": "claude-sonnet-4-6", "max_budget_usd": 5.0},
                "entity_memory": {"company_name": "Acme Corp"},
                "audit_context": {"prior_findings": [], "risk_areas": ["billing"]},
                "file_memory": [],
            },
        )
        assert resp.status_code == 200

        resp = client.get("/api/users/alice/memory")
        data = resp.json()
        assert data["preferences"]["model"] == "claude-sonnet-4-6"
        assert data["entity_memory"]["company_name"] == "Acme Corp"
        assert "billing" in data["audit_context"]["risk_areas"]

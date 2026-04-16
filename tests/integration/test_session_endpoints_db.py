"""Integration tests for session endpoints wired to SessionStore.

Verifies that session CRUD endpoints work with SQLite-backed storage.
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
from src.database import Database
from src.session_store import SessionStore


@pytest.fixture(autouse=True)
def _setup_db_store(tmp_path: Path) -> SessionStore:
    """Set up a SQLite-backed SessionStore for the server."""
    main_server.DATA_ROOT = tmp_path
    main_server.buffer = main_server.MessageBuffer(
        base_dir=tmp_path / ".msg-buffer"
    )
    main_server.active_tasks.clear()
    main_server.pending_answers.clear()

    (tmp_path / "users").mkdir(exist_ok=True)

    # Create DB and SessionStore
    db = Database(db_path=tmp_path / "test.db")

    async def _init():
        await db.init()

    import asyncio
    asyncio.get_event_loop_policy().get_event_loop().run_until_complete(_init())

    store = SessionStore(db=db, msg_buffer_dir=tmp_path / ".msg-buffer")
    main_server.session_store = store  # type: ignore[attr-defined]

    return store


@pytest.fixture
def client() -> TestClient:
    return TestClient(main_server.app)


# ── Session endpoints via SessionStore ────────────────────────────


class TestSessionEndpoints:
    """Session CRUD operations go through SessionStore."""

    def test_create_session_writes_to_db(self, client: TestClient, _setup_db_store: SessionStore) -> None:
        resp = client.post("/api/users/alice/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        sid = data["session_id"]

        # Verify session exists in DB
        import asyncio
        loop = asyncio.get_event_loop_policy().get_event_loop()
        sessions = loop.run_until_complete(
            _setup_db_store.list_sessions("alice")
        )
        assert any(s["session_id"] == sid for s in sessions)

    def test_list_sessions_from_db(self, client: TestClient, _setup_db_store: SessionStore) -> None:
        import asyncio
        loop = asyncio.get_event_loop_policy().get_event_loop()
        loop.run_until_complete(_setup_db_store.create_session("alice", "s1"))
        loop.run_until_complete(_setup_db_store.create_session("alice", "s2"))

        resp = client.get("/api/users/alice/sessions")
        assert resp.status_code == 200
        sessions = resp.json()
        sids = {s["session_id"] for s in sessions}
        assert "s1" in sids
        assert "s2" in sids

    def test_get_history_from_db(self, client: TestClient, _setup_db_store: SessionStore) -> None:
        import asyncio
        loop = asyncio.get_event_loop_policy().get_event_loop()
        loop.run_until_complete(_setup_db_store.create_session("alice", "s1"))
        loop.run_until_complete(_setup_db_store.add_message("s1", {"type": "user", "content": "hi"}))
        loop.run_until_complete(_setup_db_store.add_message("s1", {"type": "assistant", "content": "hello"}))

        resp = client.get("/api/users/alice/sessions/s1/history")
        assert resp.status_code == 200
        msgs = resp.json()
        assert len(msgs) == 2
        assert msgs[0]["type"] == "user"
        assert msgs[1]["type"] == "assistant"

    def test_delete_session_from_db(self, client: TestClient, _setup_db_store: SessionStore) -> None:
        import asyncio
        loop = asyncio.get_event_loop_policy().get_event_loop()
        loop.run_until_complete(_setup_db_store.create_session("alice", "s1"))

        resp = client.delete("/api/users/alice/sessions/s1")
        assert resp.status_code == 200

        # Verify deleted from DB
        loop = asyncio.get_event_loop_policy().get_event_loop()
        sessions = loop.run_until_complete(_setup_db_store.list_sessions("alice"))
        assert not any(s["session_id"] == "s1" for s in sessions)

    def test_update_session_title_in_db(self, client: TestClient, _setup_db_store: SessionStore) -> None:
        import asyncio
        loop = asyncio.get_event_loop_policy().get_event_loop()
        loop.run_until_complete(_setup_db_store.create_session("alice", "s1"))

        resp = client.patch(
            "/api/users/alice/sessions/s1/title",
            json={"title": "My Chat"},
        )
        assert resp.status_code == 200

        # Verify in DB
        sessions = loop.run_until_complete(_setup_db_store.list_sessions("alice"))
        s1 = next(s for s in sessions if s["session_id"] == "s1")
        assert s1["title"] == "My Chat"

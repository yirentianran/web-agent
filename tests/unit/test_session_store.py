"""Unit tests for src/session_store.py — DB-backed session storage."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from src.database import Database
from src.session_store import SessionStore


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    """Create a temporary database."""
    database = Database(db_path=tmp_path / "test.db")
    await database.init()
    yield database
    await database.close()


@pytest.fixture
async def store(db: Database, tmp_path: Path) -> SessionStore:
    """Create a SessionStore backed by a temporary database."""
    return SessionStore(db=db, msg_buffer_dir=tmp_path / "msg-buffer")


# ── create_session ───────────────────────────────────────────────


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_creates_user_and_session(self, store: SessionStore) -> None:
        result = await store.create_session(user_id="u1", session_id="s1")
        assert result["session_id"] == "s1"

        # Verify user was created
        async with store.db.connection() as conn:
            cursor = await conn.execute("SELECT id FROM users WHERE id = ?", ("u1",))
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "u1"

        # Verify session was created
        async with store.db.connection() as conn:
            cursor = await conn.execute("SELECT id, user_id FROM sessions WHERE id = ?", ("s1",))
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "s1"
            assert row[1] == "u1"

    @pytest.mark.asyncio
    async def test_idempotent_for_same_session(self, store: SessionStore) -> None:
        """Creating the same session twice should not raise."""
        await store.create_session(user_id="u1", session_id="s1")
        result = await store.create_session(user_id="u1", session_id="s1")
        assert result["session_id"] == "s1"

    @pytest.mark.asyncio
    async def test_different_users_same_session_id(self, store: SessionStore) -> None:
        """Same session_id for different users should use REPLACE strategy."""
        await store.create_session(user_id="u1", session_id="shared")
        await store.create_session(user_id="u2", session_id="shared")
        # Both users should exist
        async with store.db.connection() as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM users")
            row = await cursor.fetchone()
            assert row[0] == 2


# ── list_sessions ────────────────────────────────────────────────


class TestListSessions:
    @pytest.mark.asyncio
    async def test_returns_empty_for_new_user(self, store: SessionStore) -> None:
        sessions = await store.list_sessions(user_id="u1")
        assert sessions == []

    @pytest.mark.asyncio
    async def test_returns_sessions_sorted_by_created_at(self, store: SessionStore) -> None:
        await store.create_session(user_id="u1", session_id="s1")
        await store.create_session(user_id="u1", session_id="s2")
        await store.create_session(user_id="u1", session_id="s3")

        sessions = await store.list_sessions(user_id="u1")
        assert len(sessions) == 3
        # Most recent first
        assert sessions[0]["session_id"] == "s3"
        assert sessions[1]["session_id"] == "s2"
        assert sessions[2]["session_id"] == "s1"

    @pytest.mark.asyncio
    async def test_filters_by_user(self, store: SessionStore) -> None:
        await store.create_session(user_id="u1", session_id="s1")
        await store.create_session(user_id="u2", session_id="s2")

        u1_sessions = await store.list_sessions(user_id="u1")
        assert len(u1_sessions) == 1
        assert u1_sessions[0]["session_id"] == "s1"

        u2_sessions = await store.list_sessions(user_id="u2")
        assert len(u2_sessions) == 1
        assert u2_sessions[0]["session_id"] == "s2"

    @pytest.mark.asyncio
    async def test_returns_session_fields(self, store: SessionStore) -> None:
        await store.create_session(user_id="u1", session_id="s1")

        sessions = await store.list_sessions(user_id="u1")
        assert len(sessions) == 1
        s = sessions[0]
        assert "session_id" in s
        assert "title" in s
        assert "status" in s
        assert "cost_usd" in s
        assert "message_count" in s
        assert "created_at" in s

    @pytest.mark.asyncio
    async def test_reflects_updated_title(self, store: SessionStore) -> None:
        await store.create_session(user_id="u1", session_id="s1")
        await store.update_session_title(user_id="u1", session_id="s1", title="My Session")

        sessions = await store.list_sessions(user_id="u1")
        assert sessions[0]["title"] == "My Session"

    @pytest.mark.asyncio
    async def test_reflects_updated_status(self, store: SessionStore) -> None:
        await store.create_session(user_id="u1", session_id="s1")
        await store.update_session_status(user_id="u1", session_id="s1", status="running")

        sessions = await store.list_sessions(user_id="u1")
        assert sessions[0]["status"] == "running"

    @pytest.mark.asyncio
    async def test_reflects_updated_cost(self, store: SessionStore) -> None:
        await store.create_session(user_id="u1", session_id="s1")
        await store.update_session_cost(user_id="u1", session_id="s1", cost_usd=0.042)

        sessions = await store.list_sessions(user_id="u1")
        assert sessions[0]["cost_usd"] == pytest.approx(0.042, rel=1e-4)

    @pytest.mark.asyncio
    async def test_reflects_updated_message_count(self, store: SessionStore) -> None:
        await store.create_session(user_id="u1", session_id="s1")
        await store.update_session_stats(user_id="u1", session_id="s1", message_count=42, cost_usd=0.01)

        sessions = await store.list_sessions(user_id="u1")
        assert sessions[0]["message_count"] == 42


# ── get_session_history ──────────────────────────────────────────


class TestGetSessionHistory:
    @pytest.mark.asyncio
    async def test_returns_empty_for_new_session(self, store: SessionStore) -> None:
        await store.create_session(user_id="u1", session_id="s1")
        history = await store.get_session_history(session_id="s1")
        assert history == []

    @pytest.mark.asyncio
    async def test_returns_messages_ordered_by_seq(self, store: SessionStore) -> None:
        await store.create_session(user_id="u1", session_id="s1")
        await store.add_message(session_id="s1", message={"type": "user", "content": "hello"})
        await store.add_message(session_id="s1", message={"type": "assistant", "content": "hi"})

        history = await store.get_session_history(session_id="s1")
        assert len(history) == 2
        assert history[0]["type"] == "user"
        assert history[1]["type"] == "assistant"

    @pytest.mark.asyncio
    async def test_respects_after_index(self, store: SessionStore) -> None:
        await store.create_session(user_id="u1", session_id="s1")
        for i in range(5):
            await store.add_message(session_id="s1", message={"type": "user", "content": f"msg-{i}"})

        history = await store.get_session_history(session_id="s1", after_index=3)
        assert len(history) == 2
        assert history[0]["content"] == "msg-3"
        assert history[1]["content"] == "msg-4"

    @pytest.mark.asyncio
    async def test_preserves_complex_messages(self, store: SessionStore) -> None:
        await store.create_session(user_id="u1", session_id="s1")
        complex_msg = {
            "type": "tool_use",
            "name": "Bash",
            "content": "echo hello",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        await store.add_message(session_id="s1", message=complex_msg)

        history = await store.get_session_history(session_id="s1")
        assert len(history) == 1
        assert history[0]["type"] == "tool_use"
        assert history[0]["name"] == "Bash"

    @pytest.mark.asyncio
    async def test_tool_use_preserves_id_and_input(self, store: SessionStore) -> None:
        """tool_use messages store their id and input in the payload JSON.
        get_session_history must expose these as top-level fields so the
        frontend can render tool call bubbles on page refresh."""
        await store.create_session(user_id="u1", session_id="s1")
        tool_use_msg = {
            "type": "tool_use",
            "name": "Bash",
            "id": "toolu_abc123",
            "input": {"command": "echo hello", "description": "Print hello"},
        }
        await store.add_message(session_id="s1", message=tool_use_msg)

        history = await store.get_session_history(session_id="s1")
        assert len(history) == 1
        msg = history[0]
        assert msg["type"] == "tool_use"
        assert msg["name"] == "Bash"
        assert msg.get("id") == "toolu_abc123", "tool_use id must be exposed for frontend rendering"
        assert "input" in msg, "tool_use input must be exposed for frontend rendering"
        assert msg["input"]["command"] == "echo hello"
        assert msg["input"]["description"] == "Print hello"

    @pytest.mark.asyncio
    async def test_tool_result_preserves_tool_use_id(self, store: SessionStore) -> None:
        """tool_result messages store tool_use_id in the payload JSON.
        get_session_history must expose it as a top-level field."""
        await store.create_session(user_id="u1", session_id="s1")
        tool_result_msg = {
            "type": "tool_result",
            "name": "Bash",
            "tool_use_id": "toolu_abc123",
            "content": "hello",
        }
        await store.add_message(session_id="s1", message=tool_result_msg)

        history = await store.get_session_history(session_id="s1")
        assert len(history) == 1
        msg = history[0]
        assert msg["type"] == "tool_result"
        assert msg["name"] == "Bash"
        assert msg.get("tool_use_id") == "toolu_abc123", "tool_result tool_use_id must be exposed"
        assert msg["content"] == "hello"

    @pytest.mark.asyncio
    async def test_file_result_preserves_data_field(self, store: SessionStore) -> None:
        """file_result messages store their file list in payload["data"].
        get_session_history must expose this as a top-level "data" field
        so the frontend receives it correctly on page refresh replay."""
        await store.create_session(user_id="u1", session_id="s1")
        file_result_msg = {
            "type": "file_result",
            "content": "",
            "session_id": "s1",
            "user_id": "u1",
            "data": [
                {
                    "filename": "index.html",
                    "size": 1024,
                    "download_url": "/api/users/u1/download/outputs/index.html",
                },
                {
                    "filename": "styles.css",
                    "size": 512,
                    "download_url": "/api/users/u1/download/outputs/styles.css",
                },
            ],
        }
        await store.add_message(session_id="s1", message=file_result_msg)

        history = await store.get_session_history(session_id="s1")
        assert len(history) == 1
        msg = history[0]
        assert msg["type"] == "file_result"
        assert "data" in msg, "file_result must expose data field for frontend rendering"
        assert len(msg["data"]) == 2
        assert msg["data"][0]["filename"] == "index.html"
        assert msg["data"][1]["filename"] == "styles.css"

    @pytest.mark.asyncio
    async def test_file_result_ordering_before_completed(self, store: SessionStore) -> None:
        """file_result should appear before session_state_changed:completed
        in the history, matching the emission order in run_agent_task."""
        await store.create_session(user_id="u1", session_id="s1")
        await store.add_message(session_id="s1", message={
            "type": "file_result",
            "content": "",
            "data": [{"filename": "app.py"}],
        })
        await store.add_message(session_id="s1", message={
            "type": "system",
            "subtype": "session_state_changed",
            "state": "completed",
        })

        history = await store.get_session_history(session_id="s1")
        assert len(history) == 2
        assert history[0]["type"] == "file_result"
        assert history[1]["type"] == "system"
        assert history[1]["subtype"] == "session_state_changed"
        # Verify data field is accessible
        assert history[0]["data"][0]["filename"] == "app.py"


# ── delete_session ───────────────────────────────────────────────


class TestDeleteSession:
    @pytest.mark.asyncio
    async def test_deletes_session_and_messages(self, store: SessionStore) -> None:
        await store.create_session(user_id="u1", session_id="s1")
        await store.add_message(session_id="s1", message={"type": "user", "content": "hi"})
        await store.delete_session(session_id="s1")

        history = await store.get_session_history(session_id="s1")
        assert history == []

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises(self, store: SessionStore) -> None:
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await store.delete_session(session_id="nonexistent")


# ── update_session_title ─────────────────────────────────────────


class TestUpdateSessionTitle:
    @pytest.mark.asyncio
    async def test_updates_title(self, store: SessionStore) -> None:
        await store.create_session(user_id="u1", session_id="s1")
        await store.update_session_title(user_id="u1", session_id="s1", title="New Title")

        async with store.db.connection() as conn:
            cursor = await conn.execute("SELECT title FROM sessions WHERE id = ?", ("s1",))
            row = await cursor.fetchone()
            assert row[0] == "New Title"

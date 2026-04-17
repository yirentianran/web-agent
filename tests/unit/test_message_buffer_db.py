"""Integration tests for MessageBuffer + SQLite dual-write."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.database import Database
from src.message_buffer import MessageBuffer


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(db_path=tmp_path / "test.db")
    await database.init()
    yield database
    await database.close()


@pytest.fixture
def buffer(tmp_path: Path) -> MessageBuffer:
    return MessageBuffer(base_dir=tmp_path / "msg-buffer")


class TestMessageBufferDBWrite:
    """Test MessageBuffer writes messages to SQLite when db is attached."""

    @pytest.mark.asyncio
    async def test_add_message_writes_to_db(
        self, buffer: MessageBuffer, db: Database
    ) -> None:
        buffer.db = db
        buffer.add_message("s1", {"type": "user", "content": "hello"})

        async with db.connection() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?", ("s1",)
            )
            row = await cursor.fetchone()
            assert row[0] == 1

    @pytest.mark.asyncio
    async def test_add_multiple_messages_writes_all_to_db(
        self, buffer: MessageBuffer, db: Database
    ) -> None:
        buffer.db = db
        for i in range(5):
            buffer.add_message("s1", {"type": "user", "content": f"msg-{i}"})

        async with db.connection() as conn:
            cursor = await conn.execute(
                "SELECT content FROM messages WHERE session_id = ? ORDER BY seq",
                ("s1",),
            )
            rows = await cursor.fetchall()
            assert len(rows) == 5
            assert rows[0][0] == "msg-0"
            assert rows[4][0] == "msg-4"

    @pytest.mark.asyncio
    async def test_add_message_still_writes_to_disk(
        self, buffer: MessageBuffer, db: Database, tmp_path: Path
    ) -> None:
        """DB write should not replace disk write — both should coexist."""
        buffer.db = db
        buffer.add_message("s1", {"type": "user", "content": "dual-write"})

        disk_path = buffer._disk_path("s1")
        assert disk_path.exists()
        content = disk_path.read_text()
        assert "dual-write" in content

    @pytest.mark.asyncio
    async def test_message_seq_assigned_correctly(
        self, buffer: MessageBuffer, db: Database
    ) -> None:
        buffer.db = db
        for i in range(3):
            buffer.add_message("s1", {"type": "user", "content": f"msg-{i}"})

        async with db.connection() as conn:
            cursor = await conn.execute(
                "SELECT seq, content FROM messages WHERE session_id = ? ORDER BY seq",
                ("s1",),
            )
            rows = await cursor.fetchall()
            assert rows[0][0] == 0
            assert rows[1][0] == 1
            assert rows[2][0] == 2

    @pytest.mark.asyncio
    async def test_no_db_still_works(self, buffer: MessageBuffer) -> None:
        """MessageBuffer should work without db attached (backward compat)."""
        buffer.add_message("s1", {"type": "user", "content": "no-db"})
        history = buffer.get_history("s1")
        assert len(history) == 1
        assert history[0]["content"] == "no-db"

    @pytest.mark.asyncio
    async def test_complex_message_stored_in_db(
        self, buffer: MessageBuffer, db: Database
    ) -> None:
        buffer.db = db
        msg = {
            "type": "tool_use",
            "name": "Bash",
            "content": "echo hello",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        buffer.add_message("s1", message=msg)

        async with db.connection() as conn:
            cursor = await conn.execute(
                "SELECT type, name, content, usage FROM messages WHERE session_id = ?",
                ("s1",),
            )
            row = await cursor.fetchone()
            assert row[0] == "tool_use"
            assert row[1] == "Bash"
            assert row[2] == "echo hello"
            assert json.loads(row[3]) == {"input_tokens": 100, "output_tokens": 50}

    @pytest.mark.asyncio
    async def test_seq_respects_existing_db_messages(
        self, buffer: MessageBuffer, db: Database
    ) -> None:
        """When DB already has messages (e.g. after migration), _write_db_sync
        should start from the next available seq, not from 0.

        This simulates: migration imports seq 0,1,2 → server restarts →
        _seq dict is empty → new message should get seq=3, not seq=0.
        """
        # Seed DB with existing messages (simulating migration)
        import sqlite3
        import time as _time
        conn = sqlite3.connect(str(db.db_path))
        for i in range(3):
            conn.execute(
                """INSERT INTO messages
                   (session_id, seq, type, subtype, name, content, payload, usage, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "migrated-session",
                    i,
                    "user",
                    None,
                    None,
                    f"pre-existing-{i}",
                    json.dumps({"type": "user", "content": f"pre-existing-{i}"}),
                    None,
                    _time.time(),
                ),
            )
        conn.commit()
        conn.close()

        # Fresh buffer — _seq is empty, simulating server restart
        buffer.db = db
        assert "migrated-session" not in buffer._seq  # sanity: no in-memory state

        # This should NOT raise UNIQUE constraint error
        buffer.add_message("migrated-session", {"type": "user", "content": "new-message"})

        # Verify new message got seq=3, not seq=0
        async with db.connection() as conn:
            cursor = await conn.execute(
                "SELECT seq, content FROM messages "
                "WHERE session_id = ? ORDER BY seq",
                ("migrated-session",),
            )
            rows = await cursor.fetchall()
            assert len(rows) == 4
            assert rows[3][0] == 3
            assert rows[3][1] == "new-message"


class TestMessageBufferDBToolUseFields:
    """Test that tool_use id and input survive the full round-trip:
    MessageBuffer.add_message → SQLite → SessionStore.get_session_history."""

    @pytest.mark.asyncio
    async def test_tool_use_id_and_input_in_db(
        self, buffer: MessageBuffer, db: Database
    ) -> None:
        """MessageBuffer._write_db_sync must persist the full message JSON
        in the payload column, including id and input."""
        buffer.db = db
        msg = {
            "type": "tool_use",
            "name": "Bash",
            "id": "toolu_abc123",
            "input": {"command": "echo hello", "description": "Print hello"},
        }
        buffer.add_message("s1", message=msg)

        # Verify the payload column contains id and input
        async with db.connection() as conn:
            cursor = await conn.execute(
                "SELECT payload FROM messages WHERE session_id = ?", ("s1",)
            )
            row = await cursor.fetchone()
            payload = json.loads(row[0])
            assert payload["id"] == "toolu_abc123"
            assert payload["input"]["command"] == "echo hello"

    @pytest.mark.asyncio
    async def test_tool_use_full_roundtrip(
        self, buffer: MessageBuffer, db: Database
    ) -> None:
        """Full round-trip: MessageBuffer writes → SessionStore reads back
        with id and input exposed as top-level fields."""
        buffer.db = db

        from src.session_store import SessionStore

        store = SessionStore(db=db, msg_buffer_dir=buffer.base_dir.parent / "msg-buffer2")

        # Simulate what the real server does: write to buffer, then read via store
        buffer.add_message("s1", {
            "type": "tool_use",
            "name": "Read",
            "id": "toolu_read42",
            "input": {"file_path": "/etc/hosts"},
        })

        # Read via the store (simulates page refresh)
        history = await store.get_session_history(session_id="s1")
        assert len(history) == 1
        msg = history[0]
        assert msg["type"] == "tool_use"
        assert msg["name"] == "Read"
        assert msg.get("id") == "toolu_read42"
        assert "input" in msg
        assert msg["input"]["file_path"] == "/etc/hosts"

    @pytest.mark.asyncio
    async def test_tool_result_full_roundtrip(
        self, buffer: MessageBuffer, db: Database
    ) -> None:
        """tool_result tool_use_id survives full round-trip."""
        buffer.db = db

        from src.session_store import SessionStore

        store = SessionStore(db=db, msg_buffer_dir=buffer.base_dir.parent / "msg-buffer3")

        buffer.add_message("s1", {
            "type": "tool_result",
            "name": "Bash",
            "tool_use_id": "toolu_abc123",
            "content": "hello world",
        })

        history = await store.get_session_history(session_id="s1")
        assert len(history) == 1
        msg = history[0]
        assert msg["type"] == "tool_result"
        assert msg["name"] == "Bash"
        assert msg.get("tool_use_id") == "toolu_abc123"
        assert msg["content"] == "hello world"

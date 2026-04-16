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

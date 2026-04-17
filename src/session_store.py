"""Database-backed session storage with file fallback during migration.

Provides session CRUD operations backed by SQLite, replacing the O(N)
file scan pattern used by the legacy file-based storage.

Usage:
    from src.session_store import SessionStore

    store = SessionStore(db=database, msg_buffer_dir=tmp_path / "msg-buffer")
    await store.create_session(user_id="u1", session_id="s1")
    sessions = await store.list_sessions(user_id="u1")
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from src.database import Database


class SessionStore:
    """Session storage backed by SQLite."""

    def __init__(self, db: Database, msg_buffer_dir: Path | None = None) -> None:
        self.db = db
        self.msg_buffer_dir = msg_buffer_dir

    async def create_session(self, user_id: str, session_id: str) -> dict[str, str]:
        """Create a new session for a user. Idempotent."""
        async with self.db.connection() as conn:
            now = time.time()
            # Ensure user exists
            await conn.execute(
                "INSERT OR IGNORE INTO users (id, created_at, last_active_at) VALUES (?, ?, ?)",
                (user_id, now, now),
            )
            # Create session (REPLACE handles idempotent re-creation)
            await conn.execute(
                """INSERT OR REPLACE INTO sessions
                   (id, user_id, title, status, cost_usd, message_count, created_at, last_active_at)
                   VALUES (?, ?, '', 'idle', 0, 0, ?, ?)""",
                (session_id, user_id, now, now),
            )
            await conn.commit()

        # Also write to disk for backward compatibility during migration
        self._write_disk_session(session_id)

        return {"session_id": session_id, "title": ""}

    async def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        """List all sessions for a user, sorted by created_at DESC."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT id, title, status, cost_usd, message_count, created_at, last_active_at
                   FROM sessions WHERE user_id = ?
                   ORDER BY created_at DESC""",
                (user_id,),
            )
            rows = await cursor.fetchall()

        return [
            {
                "session_id": row[0],
                "title": row[1],
                "status": row[2],
                "cost_usd": row[3],
                "message_count": row[4],
                "created_at": row[5],
                "last_active_at": row[6],
            }
            for row in rows
        ]

    async def get_session_history(
        self, session_id: str, after_index: int = 0
    ) -> list[dict[str, Any]]:
        """Get all messages for a session, ordered by seq."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT type, subtype, name, content, payload, usage, seq
                   FROM messages WHERE session_id = ? AND seq >= ?
                   ORDER BY seq""",
                (session_id, after_index),
            )
            rows = await cursor.fetchall()

        result = []
        for row in rows:
            msg: dict[str, Any] = {
                "type": row[0],
                "seq": row[6],
            }
            if row[1] is not None:
                msg["subtype"] = row[1]
            if row[2] is not None:
                msg["name"] = row[2]
            if row[3] is not None:
                msg["content"] = row[3]
            if row[4] is not None:
                parsed = json.loads(row[4])
                msg["payload"] = parsed
                # Map payload fields to top-level keys for specific message types
                # so the frontend receives them in the expected format.
                if msg.get("type") == "file_result" and "data" in parsed:
                    msg["data"] = parsed["data"]
                if msg.get("type") == "tool_use":
                    if "id" in parsed:
                        msg["id"] = parsed["id"]
                    if "input" in parsed:
                        msg["input"] = parsed["input"]
                if msg.get("type") == "tool_result" and "tool_use_id" in parsed:
                    msg["tool_use_id"] = parsed["tool_use_id"]
            if row[5] is not None:
                msg["usage"] = json.loads(row[5])
            result.append(msg)
        return result

    async def delete_session(self, session_id: str) -> None:
        """Delete a session and all its messages."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT id FROM sessions WHERE id = ?", (session_id,)
            )
            existing = await cursor.fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="Session not found")

            await conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            await conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            await conn.commit()

    async def update_session_title(
        self, user_id: str, session_id: str, title: str
    ) -> None:
        """Update the title of a session."""
        async with self.db.connection() as conn:
            await conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ? AND user_id = ?",
                (title, session_id, user_id),
            )
            await conn.commit()

    async def update_session_status(
        self, user_id: str, session_id: str, status: str
    ) -> None:
        """Update the status of a session."""
        async with self.db.connection() as conn:
            await conn.execute(
                "UPDATE sessions SET status = ?, last_active_at = ? "
                "WHERE id = ? AND user_id = ?",
                (status, time.time(), session_id, user_id),
            )
            await conn.commit()

    async def update_session_cost(
        self, user_id: str, session_id: str, cost_usd: float
    ) -> None:
        """Update the cost of a session."""
        async with self.db.connection() as conn:
            await conn.execute(
                "UPDATE sessions SET cost_usd = ?, last_active_at = ? "
                "WHERE id = ? AND user_id = ?",
                (cost_usd, time.time(), session_id, user_id),
            )
            await conn.commit()

    async def update_session_stats(
        self, user_id: str, session_id: str, message_count: int, cost_usd: float
    ) -> None:
        """Update message count and cost for a session."""
        async with self.db.connection() as conn:
            await conn.execute(
                "UPDATE sessions SET message_count = ?, cost_usd = ?, "
                "last_active_at = ? WHERE id = ? AND user_id = ?",
                (message_count, cost_usd, time.time(), session_id, user_id),
            )
            await conn.commit()

    async def add_message(self, session_id: str, message: dict) -> None:
        """Append a message to a session."""
        async with self.db.connection() as conn:
            # Get current max seq for this session
            cursor = await conn.execute(
                "SELECT COALESCE(MAX(seq), -1) FROM messages WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
            seq = row[0] + 1

            payload_json = None
            if message.get("payload") or (
                message.get("type") in ("tool_use", "tool_result")
                and (message.get("content") or message.get("id") or message.get("input") or message.get("tool_use_id"))
            ) or message.get("type") == "file_result":
                # file_result always stores full JSON — its data lives in
                # the "data" field, not "content" (which is empty string).
                payload_json = json.dumps(message, ensure_ascii=False)

            usage_json = None
            if message.get("usage"):
                usage_json = json.dumps(message["usage"], ensure_ascii=False)

            await conn.execute(
                """INSERT INTO messages
                   (session_id, seq, type, subtype, name, content, payload, usage, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    seq,
                    message.get("type", ""),
                    message.get("subtype"),
                    message.get("name"),
                    message.get("content"),
                    payload_json,
                    usage_json,
                    time.time(),
                ),
            )
            await conn.commit()

    # ── Internal helpers ─────────────────────────────────────────

    def _write_disk_session(self, session_id: str) -> None:
        """Write a minimal session file to disk for backward compatibility."""
        if self.msg_buffer_dir is None:
            return
        self.msg_buffer_dir.mkdir(parents=True, exist_ok=True)
        path = self.msg_buffer_dir / f"{session_id}.jsonl"
        path.touch()

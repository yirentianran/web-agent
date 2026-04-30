"""Database-backed session storage.

Provides session CRUD operations backed by SQLite.

Usage:
    from src.session_store import SessionStore

    store = SessionStore(db=database)
    await store.create_session(user_id="u1", session_id="s1")
    sessions = await store.list_sessions(user_id="u1")
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from src.database import Database

_RETRY_DELAYS = [0.1, 0.25, 0.5, 1.0, 2.0]  # exponential backoff steps

_SESSION_ID_RE = re.compile(r"^session_[a-zA-Z0-9_.-]+_\d+\.\d+_[a-f0-9]+$")


def validate_session_id(session_id: str) -> str:
    """Validate session_id format. Returns session_id or raises HTTPException(400)."""
    if not _SESSION_ID_RE.match(session_id):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid session_id format: {session_id}",
        )
    return session_id


def _is_lock_error(exc: Exception) -> bool:
    """Check if an exception is a SQLite 'database is locked' error."""
    return (
        type(exc).__name__ == "OperationalError"
        and "database is locked" in str(exc).lower()
    )


class SessionStore:
    """Session storage backed by SQLite."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def _retry_on_lock(self, operation):
        """Retry a write operation if SQLite returns 'database is locked'.

        Uses exponential backoff with _RETRY_DELAYS. Re-raises the last
        error if all retries are exhausted.
        """
        last_error = None
        for delay in _RETRY_DELAYS:
            try:
                return await operation()
            except Exception as exc:
                if _is_lock_error(exc):
                    last_error = exc
                    await asyncio.sleep(delay)
                else:
                    raise
        raise last_error  # type: ignore[misc]

    async def create_session(self, user_id: str, session_id: str) -> dict[str, str]:
        """Create a new session for a user. Idempotent."""
        async with self.db.connection() as conn:
            now = time.time()
            # Ensure user exists
            await conn.execute(
                "INSERT OR IGNORE INTO users (user_id, created_at, last_active_at) VALUES (?, ?, ?)",
                (user_id, now, now),
            )
            # Create session (REPLACE handles idempotent re-creation)
            await conn.execute(
                """INSERT OR REPLACE INTO sessions
                   (session_id, user_id, title, status, cost_usd, message_count, created_at, last_active_at)
                   VALUES (?, ?, '', 'idle', 0, 0, ?, ?)""",
                (session_id, user_id, now, now),
            )
            await conn.commit()

        return {"session_id": session_id, "title": ""}

    async def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        """List all sessions for a user, sorted by created_at DESC."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT session_id, title, status, cost_usd, message_count, created_at, last_active_at
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
        self, user_id: str, session_id: str, after_index: int = 0
    ) -> list[dict[str, Any]]:
        """Get all messages for a session, verified against user_id via JOIN."""
        async with self.db.connection() as conn:
            # Verify session belongs to user via JOIN
            cursor = await conn.execute(
                """SELECT m.type, m.subtype, m.name, m.content, m.payload, m.usage, m.seq
                   FROM messages m
                   JOIN sessions s ON m.session_id = s.session_id
                   WHERE m.session_id = ? AND s.user_id = ? AND m.seq >= ?
                   ORDER BY m.seq""",
                (session_id, user_id, after_index),
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
                if msg.get("type") == "user" and "data" in parsed:
                    msg["data"] = parsed["data"]
                if msg.get("type") == "tool_use":
                    if "id" in parsed:
                        msg["id"] = parsed["id"]
                    if "input" in parsed:
                        msg["input"] = parsed["input"]
                if msg.get("type") == "tool_result" and "tool_use_id" in parsed:
                    msg["tool_use_id"] = parsed["tool_use_id"]
                if msg.get("type") == "system" and msg.get("subtype") == "session_state_changed" and "state" in parsed:
                    msg["state"] = parsed["state"]
            if row[5] is not None:
                msg["usage"] = json.loads(row[5])
            result.append(msg)
        return result

    async def delete_session(self, user_id: str, session_id: str) -> None:
        """Delete a session and all its messages. Verifies ownership."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT session_id FROM sessions WHERE session_id = ? AND user_id = ?",
                (session_id, user_id),
            )
            existing = await cursor.fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="Session not found")

            await conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            await conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            await conn.commit()

    async def update_session_title(
        self, user_id: str, session_id: str, title: str
    ) -> None:
        """Update the title of a session."""
        async def _do():
            async with self.db.connection() as conn:
                await conn.execute(
                    "UPDATE sessions SET title = ? WHERE session_id = ? AND user_id = ?",
                    (title, session_id, user_id),
                )
                await conn.commit()

        await self._retry_on_lock(_do)

    async def update_session_status(
        self, user_id: str, session_id: str, status: str
    ) -> None:
        """Update the status of a session."""
        async def _do():
            async with self.db.connection() as conn:
                await conn.execute(
                    "UPDATE sessions SET status = ?, last_active_at = ? "
                    "WHERE session_id = ? AND user_id = ?",
                    (status, time.time(), session_id, user_id),
                )
                await conn.commit()

        await self._retry_on_lock(_do)

    async def update_session_cost(
        self, user_id: str, session_id: str, cost_usd: float
    ) -> None:
        """Update the cost of a session."""
        async def _do():
            async with self.db.connection() as conn:
                await conn.execute(
                    "UPDATE sessions SET cost_usd = ?, last_active_at = ? "
                    "WHERE session_id = ? AND user_id = ?",
                    (cost_usd, time.time(), session_id, user_id),
                )
                await conn.commit()

        await self._retry_on_lock(_do)

    async def update_session_stats(
        self, user_id: str, session_id: str, message_count: int, cost_usd: float
    ) -> None:
        """Update message count and cost for a session."""
        async def _do():
            async with self.db.connection() as conn:
                await conn.execute(
                    "UPDATE sessions SET message_count = ?, cost_usd = ?, "
                    "last_active_at = ? WHERE session_id = ? AND user_id = ?",
                    (message_count, cost_usd, time.time(), session_id, user_id),
                )
                await conn.commit()

        await self._retry_on_lock(_do)

    async def add_message(self, user_id: str, session_id: str, message: dict) -> None:
        """Append a message to a session. Verifies session belongs to user_id."""
        async def _do():
            async with self.db.connection() as conn:
                # Verify session ownership
                cursor = await conn.execute(
                    "SELECT session_id FROM sessions WHERE session_id = ? AND user_id = ?",
                    (session_id, user_id),
                )
                if await cursor.fetchone() is None:
                    raise HTTPException(status_code=404, detail="Session not found")
                # Get current max seq for this session
                cursor = await conn.execute(
                    "SELECT COALESCE(MAX(seq), -1) FROM messages WHERE session_id = ?",
                    (session_id,),
                )
                row = await cursor.fetchone()
                seq = row[0] + 1

                payload_json = None
                if message.get("payload") or (
                    message.get("type") in ("tool_use", "tool_result", "user")
                    and (message.get("content") or message.get("id") or message.get("input") or message.get("tool_use_id") or message.get("data"))
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

        await self._retry_on_lock(_do)

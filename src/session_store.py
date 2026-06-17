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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from src.database import Database

_RETRY_DELAYS = [0.1, 0.25, 0.5, 1.0, 2.0]  # exponential backoff steps

_SESSION_ID_RE = re.compile(r"^sess_[a-f0-9]{12}$")


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


def _date_to_ts(date_str: str, end_of_day: bool = False) -> float:
    """Convert a date string like '2026-06-06' to Unix timestamp."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt.replace(tzinfo=timezone.utc).timestamp()


_TOKEN_SUM = (
    "COALESCE(SUM(m.input_tokens + m.output_tokens + "
    "m.cache_read_tokens + m.cache_write_tokens), 0)"
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
                   (session_id, user_id, title, status, message_count, created_at, last_active_at)
                   VALUES (?, ?, '', 'idle', 0, ?, ?)""",
                (session_id, user_id, now, now),
            )
            await conn.commit()

        return {"session_id": session_id, "title": ""}

    async def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        """List all active sessions for a user, sorted by created_at DESC."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT session_id, title, status, message_count, created_at, last_active_at
                   FROM sessions WHERE user_id = ? AND deleted_at IS NULL
                   ORDER BY created_at DESC""",
                (user_id,),
            )
            rows = await cursor.fetchall()

        return [
            {
                "session_id": row[0],
                "title": row[1],
                "status": row[2],
                "message_count": row[3],
                "created_at": row[4],
                "last_active_at": row[5],
            }
            for row in rows
        ]

    async def list_all_sessions(
        self,
        user_id: str | None = None,
        status: str | None = None,
        q: str = "",
        from_date: str | None = None,
        to_date: str | None = None,
        sort: str = "created_at",
        order: str = "desc",
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Admin: list all sessions across users with filters + token aggregation."""
        conditions = ["s.deleted_at IS NULL"]
        params: list[Any] = []

        if user_id:
            conditions.append("s.user_id = ?")
            params.append(user_id)
        if status:
            conditions.append("s.status = ?")
            params.append(status)
        if q:
            conditions.append(
                "(s.session_id LIKE ? OR s.title LIKE ? OR s.user_id LIKE ?)"
            )
            params.extend([f"%{q}%"] * 3)
        if from_date:
            conditions.append("s.created_at >= ?")
            params.append(_date_to_ts(from_date))
        if to_date:
            conditions.append("s.created_at <= ?")
            params.append(_date_to_ts(to_date, end_of_day=True))

        where = " AND ".join(conditions)

        sort_map = {
            "created_at": "s.created_at",
            "last_active_at": "s.last_active_at",
            "message_count": "s.message_count",
            "total_tokens": "total_tokens",
        }
        sort_col = sort_map.get(sort, "s.created_at")
        direction = "DESC" if order.lower() == "desc" else "ASC"

        count_sql = f"SELECT COUNT(*) FROM sessions s WHERE {where}"
        offset = (page - 1) * page_size
        data_sql = f"""
            SELECT s.session_id, s.user_id, s.title, s.status, s.message_count,
                   {_TOKEN_SUM} AS total_tokens,
                   s.created_at, s.last_active_at
            FROM sessions s
            LEFT JOIN messages m ON s.session_id = m.session_id
            WHERE {where}
            GROUP BY s.session_id
            ORDER BY {sort_col} {direction}
            LIMIT ? OFFSET ?
        """
        async with self.db.connection() as conn:
            cursor = await conn.execute(count_sql, params)
            row = await cursor.fetchone()
            total = row[0] if row else 0
            cursor = await conn.execute(data_sql, params + [page_size, offset])
            rows = await cursor.fetchall()

        items = [
            {
                "session_id": r[0],
                "user_id": r[1],
                "title": r[2],
                "status": r[3],
                "message_count": r[4],
                "total_tokens": r[5],
                "created_at": r[6],
                "last_active_at": r[7],
            }
            for r in rows
        ]

        return {"items": items, "total": total, "page": page, "page_size": page_size}

    async def get_sessions_aggregate(
        self, from_date: str | None = None, to_date: str | None = None
    ) -> dict[str, Any]:
        """Admin: aggregate session stats — overview, by_user, by_date."""
        cond = "WHERE s.deleted_at IS NULL"
        params: list[Any] = []
        if from_date:
            cond += " AND s.created_at >= ?"
            params.append(_date_to_ts(from_date))
        if to_date:
            cond += " AND s.created_at <= ?"
            params.append(_date_to_ts(to_date, end_of_day=True))

        async with self.db.connection() as conn:
            # Overview
            overview_sql = f"""
                SELECT COUNT(*) AS total_sessions,
                       SUM(CASE WHEN s.status='running' THEN 1 ELSE 0 END) AS active_sessions,
                       COUNT(DISTINCT s.user_id) AS total_users,
                       {_TOKEN_SUM} AS total_tokens
                FROM sessions s LEFT JOIN messages m ON s.session_id=m.session_id
                {cond}
            """
            cursor = await conn.execute(overview_sql, params)
            row = await cursor.fetchone()
            overview = {
                "total_sessions": row[0] or 0,
                "active_sessions": row[1] or 0,
                "total_users": row[2] or 0,
                "total_tokens": row[3] or 0,
            }

            # By user (top 10)
            by_user_sql = f"""
                SELECT s.user_id, COUNT(*) AS session_count,
                       SUM(s.message_count) AS message_count,
                       {_TOKEN_SUM} AS total_tokens
                FROM sessions s LEFT JOIN messages m ON s.session_id=m.session_id
                {cond}
                GROUP BY s.user_id ORDER BY session_count DESC LIMIT 10
            """
            cursor = await conn.execute(by_user_sql, params)
            by_user = [
                {"user_id": r[0], "session_count": r[1], "message_count": r[2] or 0, "total_tokens": r[3] or 0}
                for r in await cursor.fetchall()
            ]

            # By date (last 30 days)
            by_date_sql = f"""
                SELECT DATE(s.created_at, 'unixepoch') AS dt, COUNT(*) AS session_count,
                       SUM(s.message_count) AS message_count,
                       {_TOKEN_SUM} AS total_tokens
                FROM sessions s LEFT JOIN messages m ON s.session_id=m.session_id
                {cond}
                GROUP BY dt ORDER BY dt DESC LIMIT 30
            """
            cursor = await conn.execute(by_date_sql, params)
            by_date = [
                {"date": r[0], "session_count": r[1], "message_count": r[2] or 0, "total_tokens": r[3] or 0}
                for r in await cursor.fetchall()
            ]

        return {"overview": overview, "by_user": by_user, "by_date": by_date}

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

        return [self._row_to_message(row) for row in rows]

    @staticmethod
    def _row_to_message(row: Any) -> dict[str, Any]:
        """Parse a messages table row into the full message dict (shared by all read paths)."""
        msg: dict[str, Any] = {"type": row[0], "seq": row[6]}
        if row[1] is not None:
            msg["subtype"] = row[1]
        if row[2] is not None:
            msg["name"] = row[2]
        if row[3] is not None:
            msg["content"] = row[3]
        if row[4] is not None:
            parsed = json.loads(row[4])
            if msg.get("type") == "file_result" and "data" in parsed:
                msg["data"] = parsed["data"]
            if msg.get("type") == "user":
                if "data" in parsed:
                    msg["data"] = parsed["data"]
                if "client_msg_id" in parsed:
                    msg["client_msg_id"] = parsed["client_msg_id"]
            if msg.get("type") == "tool_use":
                if "id" in parsed:
                    msg["id"] = parsed["id"]
                if "input" in parsed:
                    msg["input"] = parsed["input"]
            if msg.get("type") == "tool_result":
                if "tool_use_id" in parsed:
                    msg["tool_use_id"] = parsed["tool_use_id"]
                if "is_error" in parsed:
                    msg["is_error"] = parsed["is_error"]
                if "content" in parsed:
                    msg["result_content"] = parsed["content"]
            if msg.get("type") == "result":
                if "duration_ms" in parsed:
                    msg["duration_ms"] = parsed["duration_ms"]
                if "num_turns" in parsed:
                    msg["num_turns"] = parsed["num_turns"]
                if "is_error" in parsed:
                    msg["is_error"] = parsed["is_error"]
            if msg.get("type") == "stream_event" and "event" in parsed:
                msg["event"] = parsed["event"]
            if (
                msg.get("type") == "system"
                and msg.get("subtype") == "session_state_changed"
                and "state" in parsed
            ):
                msg["state"] = parsed["state"]
        if row[5] is not None:
            msg["usage"] = json.loads(row[5])
        return msg

    async def get_messages_for_session(
        self, session_id: str, limit: int = 50, offset: int = 0,
        min_seq: int | None = None, max_seq: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get recent messages for a session without user verification (admin use).
        Optionally filter by seq range (min_seq, max_seq) for context-aware loading."""
        conditions = ["m.session_id = ?"]
        params: list[Any] = [session_id]
        if min_seq is not None:
            conditions.append("m.seq >= ?")
            params.append(min_seq)
        if max_seq is not None:
            conditions.append("m.seq <= ?")
            params.append(max_seq)
        where = " AND ".join(conditions)
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                f"""SELECT m.type, m.subtype, m.name, m.content, m.payload, m.usage, m.seq
                   FROM messages m
                   WHERE {where}
                   ORDER BY m.seq DESC
                   LIMIT ? OFFSET ?""",
                params + [limit, offset],
            )
            rows = await cursor.fetchall()

        return [self._row_to_message(row) for row in reversed(rows)]

    async def count_messages_for_session(self, session_id: str) -> int:
        """Count total messages for a session (admin use)."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def has_session_history(
        self, user_id: str, session_id: str
    ) -> bool:
        """Check whether a session has any persisted messages (lightweight count check)."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT COUNT(1) FROM messages m
                   JOIN sessions s ON m.session_id = s.session_id
                   WHERE m.session_id = ? AND s.user_id = ?""",
                (session_id, user_id),
            )
            count = await cursor.fetchone()
        return count[0] > 0

    async def delete_session(self, user_id: str, session_id: str) -> None:
        """Soft-delete a session. Messages and associated data are preserved."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT session_id FROM sessions WHERE session_id = ? AND user_id = ? AND deleted_at IS NULL",
                (session_id, user_id),
            )
            existing = await cursor.fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="Session not found")

            await conn.execute(
                "UPDATE sessions SET deleted_at = ?, status = 'deleted' "
                "WHERE session_id = ? AND user_id = ?",
                (time.time(), session_id, user_id),
            )
            await conn.commit()

    async def update_session_title(
        self, user_id: str, session_id: str, title: str
    ) -> None:
        """Update the title of a session."""
        async def _do():
            async with self.db.connection() as conn:
                await conn.execute(
                    "UPDATE sessions SET title = ? WHERE session_id = ? AND user_id = ? AND deleted_at IS NULL",
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
                    "WHERE session_id = ? AND user_id = ? AND deleted_at IS NULL",
                    (status, time.time(), session_id, user_id),
                )
                await conn.commit()

        await self._retry_on_lock(_do)

    async def update_session_stats(
        self, user_id: str, session_id: str, message_count: int
    ) -> None:
        """Update message count for a session."""
        async def _do():
            async with self.db.connection() as conn:
                await conn.execute(
                    "UPDATE sessions SET message_count = ?, "
                    "last_active_at = ? WHERE session_id = ? AND user_id = ? AND deleted_at IS NULL",
                    (message_count, time.time(), session_id, user_id),
                )
                await conn.commit()

        await self._retry_on_lock(_do)

    async def add_message(self, user_id: str, session_id: str, message: dict) -> None:
        """Append a message to a session. Verifies session belongs to user_id and is not deleted."""
        if message.get("type") == "stream_event":
            return

        async def _do():
            async with self.db.connection() as conn:
                # Verify session ownership and not deleted
                cursor = await conn.execute(
                    "SELECT session_id FROM sessions WHERE session_id = ? AND user_id = ? AND deleted_at IS NULL",
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
                    message.get("type") in ("tool_use", "tool_result", "user", "stream_event")
                    and (message.get("content") or message.get("id") or message.get("input") or message.get("tool_use_id") or message.get("data") or message.get("event"))
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

    async def backfill_session_stats(self) -> None:
        """Recompute status and message_count from messages table for all sessions.

        Called once at startup to fix sessions created before real-time stats
        syncing was added. After this, _write_db and add_message keep the
        sessions table up-to-date.
        """
        async with self.db.connection() as conn:
            await conn.execute(
                """UPDATE sessions SET message_count = (
                    SELECT COUNT(*) FROM messages m
                    WHERE m.session_id = sessions.session_id
                ) WHERE deleted_at IS NULL"""
            )
            await conn.execute(
                """UPDATE sessions SET status = COALESCE(
                    (SELECT json_extract(m.payload, '$.state')
                     FROM messages m
                     WHERE m.session_id = sessions.session_id
                       AND m.type = 'system'
                       AND m.subtype = 'session_state_changed'
                     ORDER BY m.seq DESC LIMIT 1),
                    CASE
                        WHEN EXISTS (
                            SELECT 1 FROM messages m
                            WHERE m.session_id = sessions.session_id
                              AND m.type = 'result'
                        ) THEN 'completed'
                        WHEN EXISTS (
                            SELECT 1 FROM messages m
                            WHERE m.session_id = sessions.session_id
                              AND m.type = 'system'
                              AND m.subtype = 'progress'
                        ) THEN 'running'
                        WHEN (SELECT COUNT(*) FROM messages m
                              WHERE m.session_id = sessions.session_id) > 0
                        THEN 'idle'
                        ELSE sessions.status
                    END
                ) WHERE deleted_at IS NULL"""
            )
            await conn.commit()

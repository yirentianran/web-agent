"""In-memory message buffer with SQLite persistence.

Memory layer for real-time push, SQLite for disconnect recovery
and container restart resilience.

All DB access goes through the single aiosqlite connection managed by
the Database class — no more sync sqlite3 connections. add_message
writes to DB directly, eliminating the async drain queue and the
sync/async connection contention that caused "database is locked".
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.database import Database

logger = logging.getLogger(__name__)

MAX_HISTORY = 500  # max messages kept in memory per session
BUFFER_TIMEOUT = 3600  # seconds before in-memory cache is evicted
STALE_THRESHOLD = 60  # seconds of inactivity before session is considered stale
HEARTBEAT_INTERVAL = 30  # seconds between heartbeat signals


def make_heartbeat(agent_alive: bool = True) -> dict[str, Any]:
    """Create a heartbeat message to signal the session is still alive."""
    return {
        "type": "heartbeat",
        "timestamp": time.time(),
        "agent_alive": agent_alive,
    }


class MessageBuffer:
    """Per-session message cache with SQLite persistence.

    All DB reads and writes go through the Database's single aiosqlite
    connection. No sync sqlite3 connections are opened — this eliminates
    write-lock contention entirely.
    """

    def __init__(self, db: Database | None = None) -> None:
        self.db: Database | None = db
        # session_id -> state dict
        self.sessions: dict[str, dict[str, Any]] = {}
        # Track per-session sequence numbers
        self._seq: dict[str, int] = {}

    # ── internal async DB helpers ──────────────────────────────────

    async def _read_db_state(
        self, session_id: str, user_id: str | None = None
    ) -> tuple[str, bool, float]:
        """Read the last session_state_changed or result message from SQLite
        to reconstruct the buffer state after a cold start.

        Returns (state, done, cost_usd).
        """
        if self.db is None:
            return ("idle", False, 0.0)
        try:
            async with self.db.connection() as conn:
                if user_id is not None:
                    cursor = await conn.execute(
                        """SELECT m.payload
                           FROM messages m JOIN sessions s ON m.session_id = s.session_id
                           WHERE m.session_id = ? AND s.user_id = ?
                           AND (m.type = 'system' AND m.subtype = 'session_state_changed'
                                OR m.type = 'result')
                           ORDER BY m.seq DESC LIMIT 1""",
                        (session_id, user_id),
                    )
                else:
                    cursor = await conn.execute(
                        """SELECT payload
                           FROM messages
                           WHERE session_id = ?
                           AND (type = 'system' AND subtype = 'session_state_changed'
                                OR type = 'result')
                           ORDER BY seq DESC LIMIT 1""",
                        (session_id,),
                    )
                row = await cursor.fetchone()

                if row is None or row[0] is None:
                    return ("idle", False, 0.0)

                payload = json.loads(row[0])
                msg_type = payload.get("type", "")
                if msg_type == "result":
                    return ("completed", True, 0.0)
                state = payload.get("state", "idle")
                done = state in ("completed", "error", "cancelled")
                return (state, done, 0.0)
        except Exception:
            logger.warning("_read_db_state: error for session=%s", session_id, exc_info=True)
            return ("idle", False, 0.0)

    async def _get_db_owner(self, session_id: str) -> str | None:
        """Get the owning user_id of a session from the database."""
        if self.db is None:
            return None
        try:
            async with self.db.connection() as conn:
                cursor = await conn.execute(
                    "SELECT user_id FROM sessions WHERE session_id = ?",
                    (session_id,),
                )
                row = await cursor.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    async def _read_db_messages(
        self, session_id: str, after_index: int = 0, user_id: str | None = None
    ) -> list[dict]:
        """Read messages from SQLite via the async Database connection."""
        if self.db is None:
            return []

        try:
            async with self.db.connection() as conn:
                if user_id is not None:
                    cursor = await conn.execute(
                        "SELECT m.type, m.subtype, m.name, m.content, m.payload, m.usage "
                        "FROM messages m JOIN sessions s ON m.session_id = s.session_id "
                        "WHERE m.session_id = ? AND s.user_id = ? AND m.seq >= ? ORDER BY m.seq",
                        (session_id, user_id, after_index),
                    )
                else:
                    cursor = await conn.execute(
                        "SELECT type, subtype, name, content, payload, usage "
                        "FROM messages WHERE session_id = ? AND seq >= ? ORDER BY seq",
                        (session_id, after_index),
                    )
                rows = await cursor.fetchall()
        except Exception:
            return []

        result: list[dict] = []
        for row in rows:
            msg: dict[str, Any] = {"type": row[0]}
            if row[1] is not None:
                msg["subtype"] = row[1]
            if row[2] is not None:
                msg["name"] = row[2]
            if row[3] is not None:
                msg["content"] = row[3]
            if row[4] is not None:
                parsed = json.loads(row[4])
                if msg["type"] == "file_result" and "data" in parsed:
                    msg["data"] = parsed["data"]
                if msg["type"] == "user" and "data" in parsed:
                    msg["data"] = parsed["data"]
                if msg["type"] == "tool_use":
                    if "id" in parsed:
                        msg["id"] = parsed["id"]
                    if "input" in parsed:
                        msg["input"] = parsed["input"]
                if msg["type"] == "tool_result" and "tool_use_id" in parsed:
                    msg["tool_use_id"] = parsed["tool_use_id"]
                if msg["type"] == "stream_event" and "event" in parsed:
                    msg["event"] = parsed["event"]
                if (
                    msg["type"] == "system"
                    and msg.get("subtype") == "session_state_changed"
                    and "state" in parsed
                ):
                    msg["state"] = parsed["state"]
            if row[5] is not None:
                msg["usage"] = json.loads(row[5])
            result.append(msg)
        return result

    async def _write_db(self, session_id: str, message: dict) -> None:
        """Write one message to SQLite via the async connection."""
        if self.db is None:
            return

        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT COALESCE(MAX(seq), -1) FROM messages WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
            db_max_seq = row[0] if row else -1

            next_seq = max(self._seq.get(session_id, 0), db_max_seq + 1)
            self._seq[session_id] = next_seq + 1

            usage_json = None
            if message.get("usage"):
                usage_json = json.dumps(message["usage"], ensure_ascii=False)

            await conn.execute(
                """INSERT INTO messages
                   (session_id, seq, type, subtype, name, content, payload, usage, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    next_seq,
                    message.get("type", ""),
                    message.get("subtype"),
                    message.get("name"),
                    message.get("content"),
                    json.dumps(message, ensure_ascii=False),
                    usage_json,
                    time.time(),
                ),
            )
            await conn.commit()

    async def _delete_db_by_type(self, session_id: str, msg_type: str) -> None:
        """Delete messages of a given type from SQLite."""
        if self.db is None:
            return
        async with self.db.connection() as conn:
            await conn.execute(
                "DELETE FROM messages WHERE session_id = ? AND type = ?",
                (session_id, msg_type),
            )
            await conn.commit()

    # ── internal helpers ────────────────────────────────────────────

    async def _ensure_buf(
        self, session_id: str, user_id: str | None = None
    ) -> dict[str, Any]:
        """Lazy-initialise a session buffer, restoring terminal state from DB.

        On first access, stores user_id for ownership verification.
        On subsequent accesses, verifies user_id matches the stored owner.
        """
        if session_id not in self.sessions:
            db_state, db_done, db_cost = await self._read_db_state(session_id, user_id)
            buf: dict[str, Any] = {
                "messages": [],
                "base_index": 0,
                "consumers": set(),
                "done": db_done,
                "state": db_state,
                "last_active": time.time(),
                "cost_usd": db_cost,
                "user_id": user_id,
            }
            self.sessions[session_id] = buf
        else:
            stored_user = self.sessions[session_id].get("user_id")
            # Only enforce ownership when both sides have a real user_id.
            # Empty strings / None mean "no user context" (internal calls).
            if (
                user_id
                and stored_user
                and user_id != stored_user
            ):
                raise ValueError(
                    f"Session {session_id} is owned by {stored_user}, "
                    f"cannot access as {user_id}"
                )
            if not stored_user and user_id:
                self.sessions[session_id]["user_id"] = user_id
        return self.sessions[session_id]

    def _evict_old(self, session_id: str) -> None:
        """Evict old messages when the buffer grows too large."""
        buf = self.sessions.get(session_id)
        if buf is None:
            return
        msgs = buf["messages"]
        if len(msgs) <= MAX_HISTORY:
            return
        to_drop = len(msgs) - MAX_HISTORY
        buf["base_index"] += to_drop
        buf["messages"] = msgs[to_drop:]

    # ── public API ──────────────────────────────────────────────────

    async def add_message(
        self, session_id: str, message: dict, user_id: str = ""
    ) -> None:
        """Add a message to the in-memory buffer and persist to SQLite.

        For new sessions, verifies user_id against the database before
        creating the buffer entry, preventing cross-user session hijacking.
        """
        # Resolve the true owner from DB for new buffer entries
        if session_id not in self.sessions and self.db is not None:
            db_owner = await self._get_db_owner(session_id)
            if db_owner is not None and db_owner != user_id:
                raise ValueError(
                    f"Session {session_id} is owned by {db_owner}, "
                    f"cannot add message as {user_id}"
                )

        buf = await self._ensure_buf(session_id, user_id)
        buf["messages"].append(message)
        self._evict_old(session_id)
        buf["last_active"] = time.time()

        # Persist to DB directly via the shared aiosqlite connection
        await self._write_db(session_id, message)

        # Update session state based on message type
        msg_type = message.get("type", "")
        prev_state = buf.get("state", "idle")
        if msg_type == "result" and prev_state in ("completed", "error", "cancelled"):
            pass  # Keep done=True for redundant result messages
        else:
            buf["done"] = False

        if msg_type == "system" and message.get("subtype") == "progress":
            buf["state"] = "running"
        elif msg_type == "tool_use" and message.get("name") == "AskUserQuestion":
            buf["state"] = "waiting_user"
        elif msg_type == "result":
            buf["state"] = "completed"
            buf["done"] = True

        # Accumulate cost if usage info is present
        usage = message.get("usage")
        if usage:
            from src.cost import estimate_cost

            cost = estimate_cost(
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
            )
            buf["cost_usd"] += cost

        # Wake up all waiting consumers
        for event in list(buf["consumers"]):
            event.set()

    async def remove_messages_by_type(
        self, session_id: str, msg_type: str, user_id: str | None = None
    ) -> None:
        """Remove all messages of a given type from the buffer and DB.

        Adjusts base_index so global after_index counters remain valid.
        """
        buf = await self._ensure_buf(session_id, user_id)
        old_msgs = buf["messages"]
        removed = [m for m in old_msgs if m.get("type") == msg_type]
        if not removed:
            return
        kept = [m for m in old_msgs if m.get("type") != msg_type]
        buf["messages"] = kept
        buf["base_index"] += len(removed)

        await self._delete_db_by_type(session_id, msg_type)

    async def get_history(
        self,
        session_id: str,
        after_index: int = 0,
        user_id: str | None = None,
    ) -> list[dict]:
        """Get messages for replay / reconnection.

        Falls back to SQLite when the in-memory list doesn't have enough
        history.
        """
        if (
            user_id is not None
            and self.db is not None
            and session_id not in self.sessions
        ):
            msgs = await self._read_db_messages(session_id, after_index, user_id)
            if msgs:
                buf = await self._ensure_buf(session_id, user_id)
                return msgs
            db_owner = await self._get_db_owner(session_id)
            if db_owner is not None and db_owner != user_id:
                raise ValueError(
                    f"Session {session_id} is owned by {db_owner}, "
                    f"cannot access as {user_id}"
                )
            buf = await self._ensure_buf(session_id, user_id)
            return []

        buf = await self._ensure_buf(session_id, user_id)
        messages = buf["messages"]
        base_index = buf.get("base_index", 0)
        local_index = after_index - base_index

        if 0 <= local_index < len(messages):
            return messages[local_index:]

        if self.db is not None:
            return await self._read_db_messages(session_id, after_index, user_id)

        return []

    async def get_session_state(
        self, session_id: str, user_id: str | None = None
    ) -> dict[str, Any]:
        """Return current session state snapshot."""
        buf = await self._ensure_buf(session_id, user_id)
        now = time.time()
        last_active = buf.get("last_active", 0)
        elapsed = now - last_active if last_active > 0 else 0
        is_stale = elapsed > STALE_THRESHOLD
        return {
            "state": buf.get("state", "idle"),
            "cost_usd": round(buf.get("cost_usd", 0), 4),
            "last_active": last_active,
            "buffer_age": round(elapsed, 1),
            "is_stale": is_stale,
            "stale_seconds": round(elapsed, 1) if is_stale else 0,
        }

    async def get_state(
        self, session_id: str, user_id: str | None = None
    ) -> str:
        """Return the current state string for *session_id*."""
        return (await self._ensure_buf(session_id, user_id)).get("state", "idle")

    async def mark_done(
        self, session_id: str, user_id: str | None = None
    ) -> None:
        buf = await self._ensure_buf(session_id, user_id)
        buf["done"] = True
        current_state = buf.get("state", "idle")
        if current_state not in ("cancelled", "error"):
            buf["state"] = "completed"
        for event in list(buf.get("consumers", set())):
            event.set()

    async def is_done(
        self, session_id: str, user_id: str | None = None
    ) -> bool:
        return (await self._ensure_buf(session_id, user_id)).get("done", False)

    async def cancel(self, session_id: str, user_id: str | None = None) -> None:
        """Cancel a running agent task."""
        buf = await self._ensure_buf(session_id, user_id)
        buf["state"] = "cancelled"
        await self.add_message(
            session_id,
            {
                "type": "system",
                "subtype": "session_state_changed",
                "state": "cancelled",
            },
            user_id or "",
        )
        buf["done"] = True
        for event in list(buf["consumers"]):
            event.set()

    async def subscribe(self, session_id: str, user_id: str | None = None) -> Any:
        """Create an asyncio.Event consumer for this session."""
        buf = await self._ensure_buf(session_id, user_id)
        event: asyncio.Event = asyncio.Event()
        buf["consumers"].add(event)
        return event

    def unsubscribe(self, session_id: str, event: Any) -> None:
        buf = self.sessions.get(session_id)
        if buf:
            buf["consumers"].discard(event)

    def remove_session(self, session_id: str) -> None:
        """Remove a session from the in-memory buffer (called on delete)."""
        self.sessions.pop(session_id, None)

    def cleanup_expired(self) -> None:
        """Evict in-memory sessions that have been idle too long."""
        now = time.time()
        expired = [
            sid
            for sid, buf in self.sessions.items()
            if now - buf["last_active"] > BUFFER_TIMEOUT
        ]
        for sid in expired:
            del self.sessions[sid]

    def close(self) -> None:
        """Clean up (kept for API compatibility — no async drain to stop)."""
        pass
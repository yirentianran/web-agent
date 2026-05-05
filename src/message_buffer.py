"""In-memory message buffer with SQLite persistence.

Memory layer for real-time push, SQLite for disconnect recovery
and container restart resilience.

All DB writes go through the async Database connection to avoid
concurrent write-lock contention with the main aiosqlite connection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.database import Database

logger = logging.getLogger(__name__)

MAX_HISTORY = 500  # max messages kept in memory per session
BUFFER_TIMEOUT = 3600  # seconds before in-memory cache is evicted
STALE_THRESHOLD = 60  # seconds of inactivity before session is considered stale
HEARTBEAT_INTERVAL = 30  # seconds between heartbeat signals


def make_heartbeat(agent_alive: bool = True) -> dict[str, Any]:
    """Create a heartbeat message to signal the session is still alive.

    Args:
        agent_alive: Whether the backend agent task is still running.
            Defaults to True. Set to False when the task has exited/crashed
            so the frontend can trigger recovery immediately.
    """
    return {
        "type": "heartbeat",
        "timestamp": time.time(),
        "agent_alive": agent_alive,
    }


_RETRY_DELAYS = [0.1, 0.25, 0.5, 1.0, 2.0]  # exponential backoff for lock retry


def _is_lock_error(exc: Exception) -> bool:
    """Check if an exception is a SQLite 'database is locked' error."""
    return (
        type(exc).__name__ == "OperationalError"
        and "database is locked" in str(exc).lower()
    )


class MessageBuffer:
    """Per-session message cache with SQLite persistence.

    All DB writes are enqueued and processed by a single async drain
    task that uses the Database's aiosqlite connection. This avoids
    concurrent write-lock contention between sync and async connections.
    """

    def __init__(
        self,
        db: "Database | None" = None,
    ) -> None:
        self.db: Database | None = db
        # session_id -> state dict
        self.sessions: dict[str, dict[str, Any]] = {}
        # Track per-session sequence numbers for DB writes
        self._seq: dict[str, int] = {}
        # Async write queue: (session_id, message, future)
        self._write_queue: asyncio.Queue[tuple[str, dict, asyncio.Future[bool]]] | None = None
        self._drain_task: asyncio.Task | None = None

    # ── async write infrastructure ──────────────────────────────────

    def start_drain(self) -> None:
        """Start the background drain task for async DB writes.

        Called once after the event loop is running and db is attached.
        """
        if self._write_queue is not None:
            return
        self._write_queue = asyncio.Queue()
        self._drain_task = asyncio.create_task(self._drain_loop())

    async def _drain_loop(self) -> None:
        """Background task that drains the write queue using the async DB."""
        while True:
            session_id, message, future = await self._write_queue.get()
            try:
                if session_id == "__delete__":
                    # Handle delete operations
                    success = await self._delete_db_async(
                        message["session_id"], message["msg_type"]
                    )
                    future.set_result(success)
                else:
                    success = await self._write_db_async(session_id, message)
                    future.set_result(success)
            except Exception as exc:
                future.set_result(False)
                logger.error("MessageBuffer: async drain error: %s", exc)

    async def _delete_db_async(self, session_id: str, msg_type: str) -> bool:
        """Delete messages of a given type from SQLite via async connection."""
        if self.db is None:
            return False
        try:
            async with self.db.connection() as conn:
                await conn.execute(
                    "DELETE FROM messages WHERE session_id = ? AND type = ?",
                    (session_id, msg_type),
                )
                await conn.commit()
            return True
        except Exception as exc:
            logger.warning("MessageBuffer: async delete failed for session %s: %s", session_id, exc)
            return False

    async def _write_db_async(self, session_id: str, message: dict) -> bool:
        """Write one message to SQLite via the async Database connection.

        Retries on transient "database is locked" errors with exponential
        backoff, matching SessionStore._retry_on_lock behavior.
        """
        if self.db is None:
            return False

        last_error = None
        for delay in _RETRY_DELAYS:
            try:
                async with self.db.connection() as conn:
                    # Determine next seq
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

                # Success — clear any previous failure flag
                buf = self.sessions.get(session_id)
                if buf:
                    buf.pop("db_failed", None)
                return True

            except Exception as exc:
                if _is_lock_error(exc):
                    last_error = exc
                    await asyncio.sleep(delay)
                else:
                    logger.warning("MessageBuffer: async write failed for session %s: %s", session_id, exc)
                    buf = self.sessions.get(session_id)
                    if buf:
                        buf["db_failed"] = True
                        buf.setdefault("unpersisted_messages", []).append(message)
                    return False

        logger.warning("MessageBuffer: async write failed for session %s after retries: %s", session_id, last_error)
        buf = self.sessions.get(session_id)
        if buf:
            buf["db_failed"] = True
            buf.setdefault("unpersisted_messages", []).append(message)
        return False

    # ── internal helpers ─────────────────────────────────────────

    def _ensure_buf(self, session_id: str, user_id: str | None = None) -> dict[str, Any]:
        """Lazy-initialise a session buffer, restoring terminal state from DB.

        On first access, stores user_id for ownership verification.
        On subsequent accesses, verifies user_id matches the stored owner.

        Note: terminal-state recovery now happens via the async drain path
        (flush_unpersisted) rather than a sync sqlite3 connection.
        """
        if session_id not in self.sessions:
            buf: dict[str, Any] = {
                "messages": [],
                "base_index": 0,
                "consumers": set(),
                "done": False,
                "state": "idle",
                "last_active": time.time(),
                "cost_usd": 0.0,
                "user_id": user_id,
            }
            self.sessions[session_id] = buf
            logger.info(
                "_ensure_buf NEW session=%s user_id=%s",
                session_id, user_id,
            )
        else:
            # Ownership verification on existing buffer
            stored_user = self.sessions[session_id].get("user_id")
            if user_id is not None and stored_user is not None and user_id != stored_user:
                logger.warning(
                    "_ensure_buf REJECT session=%s stored_user=%s request_user=%s",
                    session_id, stored_user, user_id,
                )
                raise ValueError(
                    f"Session {session_id} is owned by {stored_user}, "
                    f"cannot access as {user_id}"
                )
            # Update user_id if it was previously None
            if stored_user is None and user_id is not None:
                self.sessions[session_id]["user_id"] = user_id
            logger.info(
                "_ensure_buf OK session=%s stored_user=%s request_user=%s",
                session_id, stored_user, user_id,
            )
        return self.sessions[session_id]

    async def _read_db_async(self, session_id: str, after_index: int = 0, user_id: str | None = None) -> list[dict]:
        """Read messages from SQLite via the async Database connection.

        When user_id is provided, JOINs with sessions table to verify ownership.
        """
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
            logger.warning("MessageBuffer: async read failed for session %s", session_id)
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
                msg["payload"] = parsed
                # Map payload fields to top-level keys (same logic as session_store.py)
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
                if msg["type"] == "system" and msg.get("subtype") == "session_state_changed" and "state" in parsed:
                    msg["state"] = parsed["state"]
            if row[5] is not None:
                msg["usage"] = json.loads(row[5])
            result.append(msg)
        return result

    async def _get_db_owner(self, session_id: str) -> str | None:
        """Get the owning user_id of a session from the database.

        Returns None if the session doesn't exist or the DB is unavailable.
        """
        if self.db is None:
            logger.info("_get_db_owner: db is None for session=%s", session_id)
            return None
        try:
            async with self.db.connection() as conn:
                cursor = await conn.execute(
                    "SELECT user_id FROM sessions WHERE session_id = ?",
                    (session_id,),
                )
                row = await cursor.fetchone()
                owner = row[0] if row else None
                logger.info(
                    "_get_db_owner: session=%s owner=%s",
                    session_id, owner,
                )
                return owner
        except Exception as e:
            logger.warning("_get_db_owner: error for session=%s: %s", session_id, e)
            return None

    def _sync_db_owner(self, session_id: str) -> str | None:
        """Sync version of _get_db_owner using a direct sqlite3 connection.

        Used by add_message to verify ownership before creating a buffer entry.
        """
        if self.db is None:
            return None
        try:
            import sqlite3

            conn = sqlite3.connect(str(self.db.db_path))
            try:
                row = conn.execute(
                    "SELECT user_id FROM sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                return row[0] if row else None
            finally:
                conn.close()
        except Exception as e:
            logger.warning("_sync_db_owner: error for session=%s: %s", session_id, e)
            return None

    # ── public API ───────────────────────────────────────────────

    def remove_messages_by_type(self, session_id: str, msg_type: str, user_id: str | None = None) -> None:
        """Remove all messages of a given type from the buffer and DB.

        Adjusts base_index so global after_index counters remain valid.
        Used to dedup file_result messages across multi-turn sessions —
        only the latest file_result should exist per session.
        """
        buf = self._ensure_buf(session_id, user_id)
        old_msgs = buf["messages"]
        removed = [m for m in old_msgs if m.get("type") == msg_type]
        if not removed:
            return
        kept = [m for m in old_msgs if m.get("type") != msg_type]
        buf["messages"] = kept
        # Adjust base_index so clients tracking global indices
        # (base_index + local_position) see the same messages at the same
        # indices after removal. Removing N entries shifts all subsequent
        # entries down by N, so we add N to base_index to compensate.
        buf["base_index"] += len(removed)

        # Enqueue async DB delete
        if self.db is not None and self._write_queue is not None:
            self._enqueue_db_delete(session_id, msg_type)

    def _enqueue_db_delete(self, session_id: str, msg_type: str) -> None:
        """Enqueue a DB delete operation to be processed by the drain loop."""
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._write_queue.put_nowait(("__delete__", {"session_id": session_id, "msg_type": msg_type}, future))

    def add_message(self, session_id: str, message: dict, user_id: str) -> None:
        """SDK produces a message → write to memory + enqueue async DB write.

        For new sessions, verifies user_id against the database before creating
        the buffer entry, preventing cross-user session hijacking.
        """
        # Resolve the true owner from DB for new buffer entries
        if session_id not in self.sessions and self.db is not None:
            db_owner = self._sync_db_owner(session_id)
            if db_owner is not None and db_owner != user_id:
                logger.warning(
                    "add_message REJECT session=%s DB owner=%s != caller=%s",
                    session_id, db_owner, user_id,
                )
                raise ValueError(
                    f"Session {session_id} is owned by {db_owner}, "
                    f"cannot add message as {user_id}"
                )

        buf = self._ensure_buf(session_id, user_id)
        buf["messages"].append(message)
        self._evict_old(session_id)
        buf["last_active"] = time.time()
        # Reset done flag when a new message arrives — the session is active again.
        # Only preserve done=True when a result message is added to a session
        # that was already completed (prevents double-completion artifacts).
        msg_type = message.get("type", "")
        prev_state = buf.get("state", "idle")
        if msg_type == "result" and prev_state in ("completed", "error", "cancelled"):
            pass  # Keep done=True for redundant result messages
        else:
            buf["done"] = False

        # Enqueue async DB write via the drain loop
        if self.db is not None and self._write_queue is not None:
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._write_queue.put_nowait((session_id, message, future))

        # Update session state based on message type
        msg_type = message.get("type", "")
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

    def _evict_old(self, session_id: str) -> None:
        """Evict old messages when the buffer grows too large.

        Updates base_index so that global after_index counters
        remain valid after eviction.
        """
        buf = self.sessions.get(session_id)
        if buf is None:
            return
        msgs = buf["messages"]
        if len(msgs) <= MAX_HISTORY:
            return
        # Keep the most recent MAX_HISTORY messages
        to_drop = len(msgs) - MAX_HISTORY
        buf["base_index"] += to_drop
        buf["messages"] = msgs[to_drop:]

    async def get_history(self, session_id: str, after_index: int = 0, user_id: str | None = None) -> list[dict]:
        """Get messages for replay / reconnection.

        Falls back to SQLite when the in-memory list doesn't have enough history.
        The base_index offset ensures global after_index counters stay valid
        even after old messages are evicted.

        When user_id is provided and the session is not yet in the buffer,
        verifies ownership against the database BEFORE creating the buffer
        entry — prevents a wrong user from being assigned as owner.
        """
        # ── Buffer entry does not exist yet → verify ownership via DB first ──
        if user_id is not None and self.db is not None and session_id not in self.sessions:
            logger.info(
                "get_history: checking DB ownership for session=%s user=%s",
                session_id, user_id,
            )
            msgs = await self._read_db_async(session_id, after_index, user_id)
            if msgs:
                # Messages returned → user owns this session. Safe to create buffer.
                logger.info(
                    "get_history: DB returned %d msgs — assigning session=%s to user=%s",
                    len(msgs), session_id, user_id,
                )
                buf = self._ensure_buf(session_id, user_id)
                return msgs
            # No messages returned — could be: wrong owner, no messages, or session not in DB.
            # Check DB to distinguish "wrong owner" from "new session".
            db_owner = await self._get_db_owner(session_id)
            logger.info(
                "get_history: _get_db_owner session=%s returned=%s request_user=%s",
                session_id, db_owner, user_id,
            )
            if db_owner is not None and db_owner != user_id:
                logger.warning(
                    "get_history REJECT session=%s db_owner=%s request_user=%s",
                    session_id, db_owner, user_id,
                )
                raise ValueError(
                    f"Session {session_id} is owned by {db_owner}, "
                    f"cannot access as {user_id}"
                )
            # Session not in DB → brand-new session. Create buffer for this user.
            buf = self._ensure_buf(session_id, user_id)
            return []

        buf = self._ensure_buf(session_id, user_id)
        messages = buf["messages"]
        base_index = buf.get("base_index", 0)

        # Convert global after_index to local list position
        local_index = after_index - base_index

        if local_index >= 0 and local_index < len(messages):
            # Have messages in memory starting from the requested position
            return messages[local_index:]

        # Need to read from SQLite
        if self.db is not None:
            return await self._read_db_async(session_id, after_index, user_id)

        return []

    def get_session_state(self, session_id: str, user_id: str | None = None) -> dict[str, Any]:
        """Return current session state snapshot."""
        buf = self._ensure_buf(session_id, user_id)
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

    def get_state(self, session_id: str, user_id: str | None = None) -> str:
        """Return the current state string for *session_id* (e.g. 'running')."""
        return self._ensure_buf(session_id, user_id).get("state", "idle")

    def mark_done(self, session_id: str, user_id: str | None = None) -> None:
        self._ensure_buf(session_id, user_id)["done"] = True
        # Don't overwrite an already-set terminal state (e.g., 'cancelled'
        # from cancel()). Only set 'completed' if the session wasn't already
        # in a different terminal state.
        current_state = self.sessions[session_id].get("state", "idle")
        if current_state not in ("cancelled", "error"):
            self.sessions[session_id]["state"] = "completed"
        # Wake up waiting consumers so subscribe loop detects completion
        # immediately instead of waiting for the next 30s heartbeat.
        buf = self.sessions[session_id]
        for event in list(buf.get("consumers", set())):
            event.set()

    def is_done(self, session_id: str, user_id: str | None = None) -> bool:
        return self._ensure_buf(session_id, user_id).get("done", False)

    def cancel(self, session_id: str, user_id: str | None = None) -> None:
        """Cancel a running agent task - just sets state and wakes consumers.
        The CancelledError handler in run_agent_task will add the proper messages.
        """
        buf = self._ensure_buf(session_id, user_id)
        buf["state"] = "cancelled"
        buf["done"] = True
        # Wake up all waiting consumers so they can see the cancellation
        for event in list(buf["consumers"]):
            event.set()

    def subscribe(self, session_id: str, user_id: str | None = None) -> Any:
        """Create an asyncio.Event consumer for this session.

        The caller should await the event and then call get_history().
        """
        import asyncio

        buf = self._ensure_buf(session_id, user_id)
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
        """Evict in-memory sessions that have been idle too long.

        Disk files are preserved so history can be reloaded on demand.
        """
        now = time.time()
        expired = [
            sid
            for sid, buf in self.sessions.items()
            if now - buf["last_active"] > BUFFER_TIMEOUT
        ]
        for sid in expired:
            del self.sessions[sid]

    async def flush_unpersisted(self) -> int:
        """Retry writing unpersisted messages to SQLite via async connection.

        Called periodically or after a connection recovery. Returns the
        number of messages successfully flushed.
        """
        flushed = 0
        for sid, buf in list(self.sessions.items()):
            pending = buf.get("unpersisted_messages", [])
            if not pending:
                continue
            # Try writing each pending message
            to_retry = list(pending)
            buf["unpersisted_messages"] = []
            for msg in to_retry:
                success = await self._write_db_async(sid, msg)
                if success:
                    flushed += 1
                else:
                    # Still failing — put back in queue
                    buf["unpersisted_messages"].append(msg)
        return flushed

    def close(self) -> None:
        """Stop the drain task and clean up."""
        if self._drain_task is not None:
            self._drain_task.cancel()
            self._drain_task = None
        self._write_queue = None

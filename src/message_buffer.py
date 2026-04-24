"""In-memory message buffer with SQLite fallback for session persistence.

Memory layer for real-time push, SQLite for disconnect recovery
and container restart resilience.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.database import Database

BASE_DIR = Path("/workspace/.msg-buffer")
MAX_HISTORY = 500  # max messages kept in memory per session
BUFFER_TIMEOUT = 3600  # seconds before in-memory cache is evicted
STALE_THRESHOLD = 60  # seconds of inactivity before session is considered stale
HEARTBEAT_INTERVAL = 30  # seconds between heartbeat signals


def make_heartbeat() -> dict[str, Any]:
    """Create a heartbeat message to signal the session is still alive."""
    return {
        "type": "heartbeat",
        "timestamp": time.time(),
    }


class MessageBuffer:
    """Per-session message cache with disk persistence."""

    def __init__(
        self,
        base_dir: Path | None = None,
        db: "Database | None" = None,
    ) -> None:
        self.base_dir = base_dir or BASE_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.db: Database | None = db
        # session_id -> state dict
        self.sessions: dict[str, dict[str, Any]] = {}
        # Track per-session sequence numbers for DB writes
        self._seq: dict[str, int] = {}
        # Single cached sync connection — avoids per-message open/close overhead
        self._sync_conn: sqlite3.Connection | None = None

    # ── internal helpers ─────────────────────────────────────────

    def _ensure_buf(self, session_id: str) -> dict[str, Any]:
        """Lazy-initialise a session buffer, restoring terminal state from DB."""
        if session_id not in self.sessions:
            buf: dict[str, Any] = {
                "messages": [],
                "base_index": 0,
                "consumers": set(),
                "done": False,
                "state": "idle",
                "last_active": time.time(),
                "cost_usd": 0.0,
            }

            # On first access (e.g. after server restart), check if the
            # session had a terminal state in the database. This prevents
            # the recover loop from spinning forever on a completed session.
            if self.db is not None:
                if self._sync_conn is None:
                    try:
                        self._sync_conn = sqlite3.connect(str(self.db.db_path))
                    except Exception:
                        pass
                if self._sync_conn is not None:
                    try:
                        cursor = self._sync_conn.execute(
                            "SELECT type FROM messages WHERE session_id = ? "
                            "ORDER BY seq DESC LIMIT 1",
                            (session_id,),
                        )
                        row = cursor.fetchone()
                        if row and row[0] == "result":
                            buf["done"] = True
                            buf["state"] = "completed"
                    except Exception:
                        pass  # DB unavailable — keep defaults

            self.sessions[session_id] = buf
        return self.sessions[session_id]

    def _disk_path(self, session_id: str) -> Path:
        return self.base_dir / f"{session_id}.jsonl"

    def _write_disk(self, session_id: str, message: dict) -> None:
        """Append one message to the on-disk JSONL file."""
        path = self._disk_path(session_id)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")

    def _write_db_sync(self, session_id: str, message: dict) -> None:
        """Synchronously append one message to the SQLite database.

        Uses a single cached sync connection to avoid per-message open/close
        overhead and file-locking issues.
        """
        if self.db is None:
            return

        # Lazily create a single sync connection
        if self._sync_conn is None:
            self._sync_conn = sqlite3.connect(str(self.db.db_path))

        conn = self._sync_conn
        try:
            # Determine next seq: check DB for existing messages (e.g. after
            # migration) and use the higher of in-memory counter vs DB max.
            cursor = conn.execute(
                "SELECT COALESCE(MAX(seq), -1) FROM messages WHERE session_id = ?",
                (session_id,),
            )
            db_max_seq = cursor.fetchone()[0]

            next_seq = max(self._seq.get(session_id, 0), db_max_seq + 1)
            self._seq[session_id] = next_seq + 1

            usage_json = None
            if message.get("usage"):
                usage_json = json.dumps(message["usage"], ensure_ascii=False)

            conn.execute(
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
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _read_db_sync(self, session_id: str, after_index: int = 0) -> list[dict]:
        """Read messages from SQLite starting at *after_index*.

        Reconstructs messages from the DB schema, mirroring the field-mapping
        logic in SessionStore.get_session_history().
        """
        if self.db is None or self._sync_conn is None:
            try:
                self._sync_conn = sqlite3.connect(str(self.db.db_path))
            except Exception:
                return []

        cursor = self._sync_conn.execute(
            "SELECT type, subtype, name, content, payload, usage "
            "FROM messages WHERE session_id = ? AND seq >= ? ORDER BY seq",
            (session_id, after_index),
        )
        rows = cursor.fetchall()
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

    def _read_disk(self, session_id: str, after_index: int = 0) -> list[dict]:
        """Read messages from disk starting at *after_index*."""
        path = self._disk_path(session_id)
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return [json.loads(line) for i, line in enumerate(lines) if i >= after_index]

    # ── public API ───────────────────────────────────────────────

    def add_message(self, session_id: str, message: dict) -> None:
        """SDK produces a message → write to memory + SQLite (if DB attached)."""
        buf = self._ensure_buf(session_id)
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

        # Dual-write to SQLite if database is attached
        self._write_db_sync(session_id, message)

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

    def get_history(self, session_id: str, after_index: int = 0) -> list[dict]:
        """Get messages for replay / reconnection.

        Falls back to disk when the in-memory list doesn't have enough history.
        The base_index offset ensures global after_index counters stay valid
        even after old messages are evicted.
        """
        buf = self._ensure_buf(session_id)
        messages = buf["messages"]
        base_index = buf.get("base_index", 0)

        # Convert global after_index to local list position
        local_index = after_index - base_index

        if local_index >= 0 and local_index < len(messages):
            # Have messages in memory starting from the requested position
            return messages[local_index:]

        # Need to read from disk — prefer SQLite when DB is attached
        if self.db is not None:
            db_result = self._read_db_sync(session_id, after_index)
            if db_result:
                return db_result

        # DB unavailable or empty: fall back to JSONL disk file
        return self._read_disk(session_id, after_index)

    def get_session_state(self, session_id: str) -> dict[str, Any]:
        """Return current session state snapshot."""
        buf = self._ensure_buf(session_id)
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

    def mark_done(self, session_id: str) -> None:
        self._ensure_buf(session_id)["done"] = True
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

    def is_done(self, session_id: str) -> bool:
        return self._ensure_buf(session_id).get("done", False)

    def cancel(self, session_id: str) -> None:
        """Cancel a running agent task - just sets state and wakes consumers.
        The CancelledError handler in run_agent_task will add the proper messages.
        """
        buf = self._ensure_buf(session_id)
        buf["state"] = "cancelled"
        buf["done"] = True
        # Wake up all waiting consumers so they can see the cancellation
        for event in list(buf["consumers"]):
            event.set()

    def subscribe(self, session_id: str) -> Any:
        """Create an asyncio.Event consumer for this session.

        The caller should await the event and then call get_history().
        """
        import asyncio

        buf = self._ensure_buf(session_id)
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

    def close(self) -> None:
        """Close the cached sync database connection."""
        if self._sync_conn is not None:
            self._sync_conn.close()
            self._sync_conn = None

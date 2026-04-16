"""Disk+memory dual-layer message buffer for session persistence.

Memory layer for real-time push, disk layer for disconnect recovery
and container restart resilience.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
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

    # ── internal helpers ─────────────────────────────────────────

    def _ensure_buf(self, session_id: str) -> dict[str, Any]:
        """Lazy-initialise a session buffer."""
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "messages": deque(maxlen=MAX_HISTORY),
                "consumers": set(),
                "done": False,
                "state": "idle",  # idle | running | completed | error | waiting_user | cancelled
                "last_active": time.time(),
                "cost_usd": 0.0,
            }
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

        Uses the sync sqlite3 connection to avoid async/sync boundary issues
        when called from the synchronous add_message() method.
        """
        if self.db is None or self.db._pool is None:
            return

        seq = self._seq.get(session_id, 0)
        self._seq[session_id] = seq + 1

        usage_json = None
        if message.get("usage"):
            usage_json = json.dumps(message["usage"], ensure_ascii=False)

        # Use the underlying sync connection via aiosqlite's connection
        import sqlite3

        conn = sqlite3.connect(str(self.db.db_path))
        try:
            conn.execute(
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
                    json.dumps(message, ensure_ascii=False),
                    usage_json,
                    time.time(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

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
        """SDK produces a message → write to memory + disk (and DB if attached)."""
        buf = self._ensure_buf(session_id)
        buf["messages"].append(message)
        buf["last_active"] = time.time()
        # Reset done flag when a new message arrives — the session is active again.
        # But don't reset if the session is already in a terminal state
        # (completed/error/cancelled) — prevents undoing mark_done when
        # state_changed messages are added after it.
        state = buf.get("state", "idle")
        if state not in ("completed", "error", "cancelled"):
            buf["done"] = False
        self._write_disk(session_id, message)

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

    def insert_before_type(self, session_id: str, message: dict, before_type: str) -> None:
        """Insert a message before the first message of the given type in the buffer.

        If no message of *before_type* exists, appends normally.
        Also writes the message to disk at the insertion point.
        """
        buf = self._ensure_buf(session_id)
        buf["last_active"] = time.time()

        messages = buf["messages"]

        # If deque is at max capacity, we can't use insert(). Fall back to append.
        if len(messages) >= messages.maxlen:
            messages.append(message)
            self._write_disk(session_id, message)
            for event in list(buf["consumers"]):
                event.set()
            return

        # Find the index of the first message with the target type (search from end)
        insert_idx = len(messages)
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("type") == before_type:
                insert_idx = i
                break

        # Insert at the found position
        messages.insert(insert_idx, message)

        # Write to disk (append the inserted message)
        self._write_disk(session_id, message)

        # Wake up all waiting consumers
        for event in list(buf["consumers"]):
            event.set()

    def get_history(self, session_id: str, after_index: int = 0) -> list[dict]:
        """Get messages for replay / reconnection.

        Falls back to disk when the in-memory deque doesn't have enough history.
        """
        buf = self._ensure_buf(session_id)
        messages = list(buf["messages"])
        if len(messages) > after_index:
            return messages[after_index:]
        # Cold cache → load from disk and back-fill memory
        disk_msgs = self._read_disk(session_id, after_index)
        for msg in disk_msgs:
            buf["messages"].append(msg)
        return list(buf["messages"])[after_index:]

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
            "is_stale": is_stale,
            "stale_seconds": round(elapsed, 1) if is_stale else 0,
        }

    def mark_done(self, session_id: str) -> None:
        self._ensure_buf(session_id)["done"] = True
        self.sessions[session_id]["state"] = "completed"

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

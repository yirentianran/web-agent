"""Unit tests for MessageBuffer — disk+memory dual-layer message buffer."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

import pytest

from src.message_buffer import MessageBuffer


@pytest.fixture()
def buffer(tmp_path: Path) -> MessageBuffer:
    """Create a MessageBuffer backed by a temporary directory."""
    return MessageBuffer(base_dir=tmp_path)


# ── add_message / get_history ─────────────────────────────────────


class TestAddMessageAndGetHistory:
    def test_add_and_retrieve_single_message(self, buffer: MessageBuffer) -> None:
        buffer.add_message("s1", {"type": "user", "content": "hello"})
        history = buffer.get_history("s1")
        assert len(history) == 1
        assert history[0]["type"] == "user"
        assert history[0]["content"] == "hello"

    def test_add_multiple_messages(self, buffer: MessageBuffer) -> None:
        for i in range(5):
            buffer.add_message("s1", {"type": "user", "content": f"msg-{i}"})
        history = buffer.get_history("s1")
        assert len(history) == 5
        assert history[-1]["content"] == "msg-4"

    def test_get_history_after_index(self, buffer: MessageBuffer) -> None:
        for i in range(5):
            buffer.add_message("s1", {"type": "user", "content": f"msg-{i}"})
        # Only messages after index 2
        history = buffer.get_history("s1", after_index=2)
        assert len(history) == 3
        assert history[0]["content"] == "msg-2"

    def test_get_history_empty_buffer(self, buffer: MessageBuffer) -> None:
        assert buffer.get_history("s1") == []

    def test_get_history_after_index_beyond_length(self, buffer: MessageBuffer) -> None:
        buffer.add_message("s1", {"type": "user", "content": "only"})
        assert buffer.get_history("s1", after_index=10) == []


# ── Session state transitions ──────────────────────────────────────


class TestSessionState:
    def test_initial_state_is_idle(self, buffer: MessageBuffer) -> None:
        state = buffer.get_session_state("s1")
        assert state["state"] == "idle"

    def test_progress_message_sets_running(self, buffer: MessageBuffer) -> None:
        buffer.add_message("s1", {"type": "system", "subtype": "progress"})
        state = buffer.get_session_state("s1")
        assert state["state"] == "running"

    def test_result_message_sets_completed(self, buffer: MessageBuffer) -> None:
        buffer.add_message("s1", {"type": "result"})
        state = buffer.get_session_state("s1")
        assert state["state"] == "completed"

    def test_ask_user_question_sets_waiting_user(self, buffer: MessageBuffer) -> None:
        buffer.add_message("s1", {"type": "tool_use", "name": "AskUserQuestion"})
        state = buffer.get_session_state("s1")
        assert state["state"] == "waiting_user"


# ── Cost accumulation ─────────────────────────────────────────────


class TestCostAccumulation:
    def test_cost_accumulated_from_usage(self, buffer: MessageBuffer) -> None:
        buffer.add_message("s1", {
            "type": "result",
            "usage": {"input_tokens": 1000, "output_tokens": 500},
        })
        state = buffer.get_session_state("s1")
        # claude-sonnet-4-6: input=$3/M, output=$15/M
        # cost = 1000*3/1e6 + 500*15/1e6 = 0.003 + 0.0075 = 0.0105
        assert state["cost_usd"] == pytest.approx(0.0105, rel=1e-4)

    def test_no_usage_no_cost(self, buffer: MessageBuffer) -> None:
        buffer.add_message("s1", {"type": "user", "content": "hello"})
        state = buffer.get_session_state("s1")
        assert state["cost_usd"] == 0.0


# ── mark_done / is_done ───────────────────────────────────────────


class TestMarkDone:
    def test_mark_done_sets_flag(self, buffer: MessageBuffer) -> None:
        assert not buffer.is_done("s1")
        buffer.mark_done("s1")
        assert buffer.is_done("s1")

    def test_mark_done_also_sets_completed_state(self, buffer: MessageBuffer) -> None:
        buffer.mark_done("s1")
        state = buffer.get_session_state("s1")
        assert state["state"] == "completed"


# ── cancel ─────────────────────────────────────────────────────────


class TestCancel:
    def test_cancel_sets_cancelled_state(self, buffer: MessageBuffer) -> None:
        buffer.add_message("s1", {"type": "system", "subtype": "progress"})
        buffer.cancel("s1")
        state = buffer.get_session_state("s1")
        assert state["state"] == "cancelled"

    def test_cancel_does_not_add_messages(self, buffer: MessageBuffer) -> None:
        """cancel() only sets state — the run_agent_task CancelledError handler
        adds the system messages."""
        buffer.cancel("s1")
        history = buffer.get_history("s1")
        assert history == []

    def test_cancel_sets_done(self, buffer: MessageBuffer) -> None:
        buffer.cancel("s1")
        assert buffer.is_done("s1")


# ── subscribe / unsubscribe ────────────────────────────────────────


class TestSubscribe:
    def test_subscribe_returns_event(self, buffer: MessageBuffer) -> None:
        event = buffer.subscribe("s1")
        assert isinstance(event, asyncio.Event)

    def test_add_message_sets_event(self, buffer: MessageBuffer) -> None:
        event = buffer.subscribe("s1")
        assert not event.is_set()
        buffer.add_message("s1", {"type": "user", "content": "hello"})
        assert event.is_set()

    def test_unsubscribe_removes_event(self, buffer: MessageBuffer) -> None:
        event = buffer.subscribe("s1")
        buffer.unsubscribe("s1", event)
        buffer.add_message("s1", {"type": "user", "content": "hello"})
        # After unsubscribe, adding a message should not raise
        assert buffer.get_history("s1") == [{"type": "user", "content": "hello"}]


# ── cleanup_expired ────────────────────────────────────────────────


class TestCleanupExpired:
    def test_evicts_idle_sessions(self, buffer: MessageBuffer) -> None:
        buffer.add_message("s1", {"type": "user", "content": "hello"})
        # Manually set last_active to trigger eviction
        buffer.sessions["s1"]["last_active"] = time.time() - 3601  # > BUFFER_TIMEOUT
        buffer.cleanup_expired()
        assert "s1" not in buffer.sessions

    def test_keeps_active_sessions(self, buffer: MessageBuffer) -> None:
        buffer.add_message("s1", {"type": "user", "content": "hello"})
        buffer.cleanup_expired()
        assert "s1" in buffer.sessions

    def test_keeps_active_sessions(self, buffer: MessageBuffer) -> None:
        buffer.add_message("s1", {"type": "user", "content": "hello"})
        buffer.cleanup_expired()
        assert "s1" in buffer.sessions


# ── Restart recovery (DB state restoration) ───────────────────────


class TestRestartRecovery:
    """Simulate server restart: a session was completed before restart,
    then a new MessageBuffer is created and should restore terminal state from DB."""

    def _write_completed_session_to_db(self, db_path: Path, session_id: str) -> None:
        """Directly write messages to SQLite to simulate a completed session."""
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS messages ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  session_id TEXT NOT NULL,"
                "  seq INTEGER NOT NULL,"
                "  type TEXT NOT NULL,"
                "  subtype TEXT,"
                "  name TEXT,"
                "  content TEXT,"
                "  payload TEXT,"
                "  usage TEXT,"
                "  created_at REAL NOT NULL DEFAULT 0"
                ")"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_session_seq "
                "ON messages(session_id, seq)"
            )
            # Write a conversation that ended with a result message
            conn.execute(
                "INSERT INTO messages (session_id, seq, type, content, created_at) "
                "VALUES (?, 0, 'user', 'hello', ?)",
                (session_id, time.time() - 100),
            )
            conn.execute(
                "INSERT INTO messages (session_id, seq, type, content, created_at) "
                "VALUES (?, 1, 'system', 'working...', ?)",
                (session_id, time.time() - 50),
            )
            conn.execute(
                "INSERT INTO messages (session_id, seq, type, content, created_at) "
                "VALUES (?, 2, 'result', 'done!', ?)",
                (session_id, time.time() - 10),
            )
            conn.commit()
        finally:
            conn.close()

    def test_restores_done_true_from_db_result_message(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"
        session_id = "completed-session-1"
        self._write_completed_session_to_db(db_path, session_id)

        # Simulate restart: new MessageBuffer with DB attached
        buf = MessageBuffer(base_dir=tmp_path / "buf", db=type("FakeDB", (), {"db_path": db_path})())  # type: ignore[arg-type]
        buf._sync_conn = sqlite3.connect(str(db_path))

        # Accessing the session should restore done=True from DB
        assert buf.is_done(session_id) is True

    def test_restores_completed_state_from_db(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"
        session_id = "completed-session-2"
        self._write_completed_session_to_db(db_path, session_id)

        buf = MessageBuffer(base_dir=tmp_path / "buf", db=type("FakeDB", (), {"db_path": db_path})())  # type: ignore[arg-type]
        buf._sync_conn = sqlite3.connect(str(db_path))

        state = buf.get_session_state(session_id)
        assert state["state"] == "completed"

    def test_idle_session_stays_idle_after_restart(
        self, tmp_path: Path
    ) -> None:
        """A session with only user messages (no result) should stay idle."""
        db_path = tmp_path / "test.db"
        session_id = "idle-session-1"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS messages ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  session_id TEXT NOT NULL,"
                "  seq INTEGER NOT NULL,"
                "  type TEXT NOT NULL,"
                "  subtype TEXT,"
                "  name TEXT,"
                "  content TEXT,"
                "  payload TEXT,"
                "  usage TEXT,"
                "  created_at REAL NOT NULL DEFAULT 0"
                ")"
            )
            conn.execute(
                "INSERT INTO messages (session_id, seq, type, content, created_at) "
                "VALUES (?, 0, 'user', 'hello', ?)",
                (session_id, time.time()),
            )
            conn.commit()
        finally:
            conn.close()

        buf = MessageBuffer(base_dir=tmp_path / "buf", db=type("FakeDB", (), {"db_path": db_path})())  # type: ignore[arg-type]
        buf._sync_conn = sqlite3.connect(str(db_path))

        assert buf.is_done(session_id) is False
        state = buf.get_session_state(session_id)
        assert state["state"] == "idle"

    def test_user_message_resets_done_after_restart(
        self, tmp_path: Path
    ) -> None:
        """After restart, adding a user message to a completed session must
        reset done=False so the subscribe loop doesn't exit prematurely."""
        db_path = tmp_path / "test.db"
        session_id = "completed-session-3"
        self._write_completed_session_to_db(db_path, session_id)

        buf = MessageBuffer(base_dir=tmp_path / "buf", db=type("FakeDB", (), {"db_path": db_path})())  # type: ignore[arg-type]
        buf._sync_conn = sqlite3.connect(str(db_path))

        # Buffer was restored from DB: done=True
        assert buf.is_done(session_id) is True

        # Adding a user message must reset done=False
        buf.add_message(session_id, {"type": "user", "content": "new question"})
        assert buf.is_done(session_id) is False

        # Adding a running state_changed must keep done=False
        buf.add_message(session_id, {
            "type": "system",
            "subtype": "session_state_changed",
            "state": "running",
        })
        assert buf.is_done(session_id) is False

    def test_result_message_preserves_done_after_restart(
        self, tmp_path: Path
    ) -> None:
        """A redundant result message on an already-completed session should
        not reset done back to False."""
        db_path = tmp_path / "test.db"
        session_id = "completed-session-4"
        self._write_completed_session_to_db(db_path, session_id)

        buf = MessageBuffer(base_dir=tmp_path / "buf", db=type("FakeDB", (), {"db_path": db_path})())  # type: ignore[arg-type]
        buf._sync_conn = sqlite3.connect(str(db_path))

        assert buf.is_done(session_id) is True

        # Adding another result message should preserve done=True
        buf.add_message(session_id, {"type": "result", "content": "done again"})
        assert buf.is_done(session_id) is True

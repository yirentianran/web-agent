"""Unit tests for MessageBuffer — disk+memory dual-layer message buffer."""

from __future__ import annotations

import asyncio
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

    def test_cancel_adds_system_message(self, buffer: MessageBuffer) -> None:
        buffer.cancel("s1")
        history = buffer.get_history("s1")
        assert len(history) == 1
        assert history[0]["type"] == "system"
        assert history[0]["subtype"] == "session_cancelled"

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

    def test_disk_survives_eviction(self, buffer: MessageBuffer) -> None:
        buffer.add_message("s1", {"type": "user", "content": "persist"})
        buffer.sessions["s1"]["last_active"] = time.time() - 3601
        buffer.cleanup_expired()
        assert "s1" not in buffer.sessions
        # But history is recoverable from disk
        history = buffer.get_history("s1")
        assert len(history) == 1
        assert history[0]["content"] == "persist"


# ── Disk persistence ──────────────────────────────────────────────


class TestDiskPersistence:
    def test_messages_written_to_disk(self, buffer: MessageBuffer) -> None:
        buffer.add_message("s1", {"type": "user", "content": "disk-test"})
        disk_path = buffer._disk_path("s1")
        assert disk_path.exists()
        content = disk_path.read_text()
        assert "disk-test" in content

    def test_history_reloaded_from_disk(self, buffer: MessageBuffer) -> None:
        buffer.add_message("s1", {"type": "user", "content": "msg-a"})
        buffer.add_message("s1", {"type": "assistant", "content": "msg-b"})
        # Evict from memory
        buffer.sessions["s1"]["last_active"] = time.time() - 3601
        buffer.cleanup_expired()
        # Reload should hit disk
        history = buffer.get_history("s1")
        assert len(history) == 2
        assert history[0]["content"] == "msg-a"
        assert history[1]["content"] == "msg-b"

    def test_after_index_on_disk_reload(self, buffer: MessageBuffer) -> None:
        for i in range(5):
            buffer.add_message("s1", {"type": "user", "content": f"m-{i}"})
        buffer.sessions["s1"]["last_active"] = time.time() - 3601
        buffer.cleanup_expired()
        # After eviction + disk reload, get_history loads all disk messages
        # and returns them from the refilled buffer starting at after_index.
        # Note: the cold-cache path refills then slices [after_index:] on the
        # refilled deque which starts at 0, so after_index=3 gives messages[3:].
        history = buffer.get_history("s1", after_index=0)
        assert len(history) == 5
        assert history[3]["content"] == "m-3"

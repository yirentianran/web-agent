"""Unit tests for MessageBuffer — disk+memory dual-layer message buffer."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from src.message_buffer import MessageBuffer


@pytest.fixture()
def buffer() -> MessageBuffer:
    """Create a MessageBuffer backed by a temporary directory."""
    return MessageBuffer()


# ── add_message / get_history ─────────────────────────────────────


class TestAddMessageAndGetHistory:
    @pytest.mark.asyncio
    async def test_add_and_retrieve_single_message(self, buffer: MessageBuffer) -> None:
        await buffer.add_message("s1", {"type": "user", "content": "hello"})
        history = await buffer.get_history("s1")
        assert len(history) == 1
        assert history[0]["type"] == "user"
        assert history[0]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_add_multiple_messages(self, buffer: MessageBuffer) -> None:
        for i in range(5):
            await buffer.add_message("s1", {"type": "user", "content": f"msg-{i}"})
        history = await buffer.get_history("s1")
        assert len(history) == 5
        assert history[-1]["content"] == "msg-4"

    @pytest.mark.asyncio
    async def test_get_history_after_index(self, buffer: MessageBuffer) -> None:
        for i in range(5):
            await buffer.add_message("s1", {"type": "user", "content": f"msg-{i}"})
        # Only messages after index 2
        history = await buffer.get_history("s1", after_index=2)
        assert len(history) == 3
        assert history[0]["content"] == "msg-2"

    @pytest.mark.asyncio
    async def test_get_history_empty_buffer(self, buffer: MessageBuffer) -> None:
        assert await buffer.get_history("s1") == []

    @pytest.mark.asyncio
    async def test_get_history_after_index_beyond_length(self, buffer: MessageBuffer) -> None:
        await buffer.add_message("s1", {"type": "user", "content": "only"})
        assert await buffer.get_history("s1", after_index=10) == []


# ── Session state transitions ──────────────────────────────────────


class TestSessionState:
    @pytest.mark.asyncio
    async def test_initial_state_is_idle(self, buffer: MessageBuffer) -> None:
        state = await buffer.get_session_state("s1")
        assert state["state"] == "idle"

    @pytest.mark.asyncio
    async def test_progress_message_sets_running(self, buffer: MessageBuffer) -> None:
        await buffer.add_message("s1", {"type": "system", "subtype": "progress"})
        state = await buffer.get_session_state("s1")
        assert state["state"] == "running"

    @pytest.mark.asyncio
    async def test_result_message_sets_completed(self, buffer: MessageBuffer) -> None:
        await buffer.add_message("s1", {"type": "result"})
        state = await buffer.get_session_state("s1")
        assert state["state"] == "completed"

    @pytest.mark.asyncio
    async def test_ask_user_question_sets_waiting_user(self, buffer: MessageBuffer) -> None:
        await buffer.add_message("s1", {"type": "tool_use", "name": "AskUserQuestion"})
        state = await buffer.get_session_state("s1")
        assert state["state"] == "waiting_user"


# ── Cost accumulation ─────────────────────────────────────────────


# ── mark_done / is_done ───────────────────────────────────────────


class TestMarkDone:
    @pytest.mark.asyncio
    async def test_mark_done_sets_flag(self, buffer: MessageBuffer) -> None:
        assert not await buffer.is_done("s1")
        await buffer.mark_done("s1")
        assert await buffer.is_done("s1")

    @pytest.mark.asyncio
    async def test_mark_done_also_sets_completed_state(self, buffer: MessageBuffer) -> None:
        await buffer.mark_done("s1")
        state = await buffer.get_session_state("s1")
        assert state["state"] == "completed"

    @pytest.mark.asyncio
    async def test_mark_done_wakes_consumers(self, buffer: MessageBuffer) -> None:
        """mark_done() must wake up waiting consumers immediately, not wait
        for the next heartbeat."""
        await buffer.add_message("s1", {"type": "system", "subtype": "progress"})
        event = await buffer.subscribe("s1")
        assert not event.is_set()

        await buffer.mark_done("s1")

        # Consumer must be woken immediately — not wait 30s for heartbeat
        assert event.is_set(), "mark_done() did not wake consumers"


# ── cancel ─────────────────────────────────────────────────────────


class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_sets_cancelled_state(self, buffer: MessageBuffer) -> None:
        await buffer.add_message("s1", {"type": "system", "subtype": "progress"})
        await buffer.cancel("s1")
        state = await buffer.get_session_state("s1")
        assert state["state"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_does_not_add_messages(self, buffer: MessageBuffer) -> None:
        """cancel() adds a session_state_changed message to persist the
        cancelled state, but does not add user/assistant content messages."""
        await buffer.cancel("s1")
        history = await buffer.get_history("s1")
        # cancel() emits a session_state_changed to record the terminal state
        assert len(history) == 1
        assert history[0]["type"] == "system"
        assert history[0]["subtype"] == "session_state_changed"
        assert history[0]["state"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_sets_done(self, buffer: MessageBuffer) -> None:
        await buffer.cancel("s1")
        assert await buffer.is_done("s1")

    @pytest.mark.asyncio
    async def test_mark_done_does_not_overwrite_cancelled_state(self, buffer: MessageBuffer) -> None:
        """When cancel() sets state to 'cancelled', a subsequent mark_done()
        must NOT overwrite it to 'completed'. The cancel() call represents an
        explicit terminal state and should be preserved."""
        await buffer.add_message("s1", {"type": "system", "subtype": "progress"})
        await buffer.cancel("s1")
        assert (await buffer.get_session_state("s1"))["state"] == "cancelled"

        # mark_done() is called by the CancelledError handler — it must
        # not overwrite the already-set cancelled state.
        await buffer.mark_done("s1")
        state = await buffer.get_session_state("s1")
        assert state["state"] == "cancelled", (
            f"Expected 'cancelled' after mark_done(), got '{state['state']}'"
        )
        assert await buffer.is_done("s1") is True


# ── subscribe / unsubscribe ────────────────────────────────────────


class TestSubscribe:
    @pytest.mark.asyncio
    async def test_subscribe_returns_event(self, buffer: MessageBuffer) -> None:
        event = await buffer.subscribe("s1")
        assert isinstance(event, asyncio.Event)

    @pytest.mark.asyncio
    async def test_add_message_sets_event(self, buffer: MessageBuffer) -> None:
        event = await buffer.subscribe("s1")
        assert not event.is_set()
        await buffer.add_message("s1", {"type": "user", "content": "hello"})
        assert event.is_set()

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_event(self, buffer: MessageBuffer) -> None:
        event = await buffer.subscribe("s1")
        buffer.unsubscribe("s1", event)
        await buffer.add_message("s1", {"type": "user", "content": "hello"})
        # After unsubscribe, adding a message should not raise
        assert await buffer.get_history("s1") == [{"type": "user", "content": "hello"}]


# ── cleanup_expired ────────────────────────────────────────────────


class TestCleanupExpired:
    @pytest.mark.asyncio
    async def test_evicts_idle_sessions(self, buffer: MessageBuffer) -> None:
        await buffer.add_message("s1", {"type": "user", "content": "hello"})
        # Manually set last_active to trigger eviction
        buffer.sessions["s1"]["last_active"] = time.time() - 3601  # > BUFFER_TIMEOUT
        buffer.cleanup_expired()
        assert "s1" not in buffer.sessions

    @pytest.mark.asyncio
    async def test_keeps_active_sessions(self, buffer: MessageBuffer) -> None:
        await buffer.add_message("s1", {"type": "user", "content": "hello"})
        buffer.cleanup_expired()
        assert "s1" in buffer.sessions

    @pytest.mark.asyncio
    async def test_keeps_active_sessions(self, buffer: MessageBuffer) -> None:
        await buffer.add_message("s1", {"type": "user", "content": "hello"})
        buffer.cleanup_expired()
        assert "s1" in buffer.sessions



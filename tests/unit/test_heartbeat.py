"""Tests for heartbeat mechanism in MessageBuffer subscribe loop.

The heartbeat ensures the frontend can distinguish between:
- "Agent is working normally (just slow)"
- "Session may be stuck (no activity for a long time)"
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from src.message_buffer import MessageBuffer, HEARTBEAT_INTERVAL


@pytest.fixture()
def buffer(tmp_path: Path) -> MessageBuffer:
    return MessageBuffer(base_dir=tmp_path)


class TestHeartbeatMessage:
    """verify that the buffer can emit a heartbeat-shaped message."""

    def test_heartbeat_message_shape(self) -> None:
        """Heartbeat message should have a specific type so frontend can distinguish."""
        from src.message_buffer import make_heartbeat

        msg = make_heartbeat()
        assert msg["type"] == "heartbeat"
        assert "timestamp" in msg

    def test_heartbeat_is_visible_in_history(self, buffer: MessageBuffer) -> None:
        """Heartbeat messages should be addable and retrievable like normal messages."""
        from src.message_buffer import make_heartbeat

        buffer.add_message("s1", {"type": "system", "subtype": "progress"})
        buffer.add_message("s1", make_heartbeat())

        history = buffer.get_history("s1")
        assert len(history) == 2
        assert history[1]["type"] == "heartbeat"


class TestSessionStaleDetection:
    """verify that get_session_state reports staleness."""

    def test_fresh_session_not_stale(self, buffer: MessageBuffer) -> None:
        buffer.add_message("s1", {"type": "system", "subtype": "progress"})
        state = buffer.get_session_state("s1")
        assert state["is_stale"] is False

    def test_idle_session_becomes_stale(self, buffer: MessageBuffer) -> None:
        buffer.add_message("s1", {"type": "system", "subtype": "progress"})
        # Manually set last_active to simulate long inactivity
        buffer.sessions["s1"]["last_active"] = time.time() - 120  # 2 minutes ago
        state = buffer.get_session_state("s1")
        assert state["is_stale"] is True
        assert state["stale_seconds"] >= 60

    def test_stale_threshold_is_60_seconds(self, buffer: MessageBuffer) -> None:
        from src.message_buffer import STALE_THRESHOLD

        assert STALE_THRESHOLD == 60

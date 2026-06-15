"""Unit tests for heartbeat starvation prevention.

Verifies that _maybe_send_heartbeat correctly throttles heartbeats
based on elapsed time, preventing starvation when buffer events
arrive faster than the heartbeat interval.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.message_buffer import HEARTBEAT_INTERVAL


class TestMaybeSendHeartbeat:
    """Tests for _maybe_send_heartbeat helper function."""

    @pytest.fixture()
    def mock_buffer(self) -> MagicMock:
        buf = MagicMock()
        buf.get_state = AsyncMock(return_value="running")
        return buf

    @pytest.fixture()
    def mock_websocket(self) -> MagicMock:
        ws = MagicMock()
        return ws

    @pytest.fixture()
    def active_tasks_running(self) -> dict:
        task = MagicMock()
        task.done.return_value = False
        return {"task_sess1": task}

    @pytest.fixture()
    def active_tasks_done(self) -> dict:
        task = MagicMock()
        task.done.return_value = True
        return {"task_sess1": task}

    async def test_sends_heartbeat_when_never_sent(
        self, mock_buffer, mock_websocket, active_tasks_running
    ) -> None:
        """First call with last_hb_time=0 should always send."""
        from main_server import _maybe_send_heartbeat

        with patch("main_server._safe_ws_send", new_callable=AsyncMock, return_value=True):
            new_time, ok = await _maybe_send_heartbeat(
                last_hb_time=0.0,
                session_id="sess1",
                last_seen=10,
                active_tasks=active_tasks_running,
                buffer=mock_buffer,
                websocket=mock_websocket,
            )

        assert ok is True
        assert new_time > 0

    async def test_skips_heartbeat_within_interval(
        self, mock_buffer, mock_websocket, active_tasks_running
    ) -> None:
        """Should NOT send if last heartbeat was recent."""
        from main_server import _maybe_send_heartbeat

        recent_time = time.monotonic()  # just now

        with patch("main_server._safe_ws_send", new_callable=AsyncMock) as mock_send:
            new_time, ok = await _maybe_send_heartbeat(
                last_hb_time=recent_time,
                session_id="sess1",
                last_seen=10,
                active_tasks=active_tasks_running,
                buffer=mock_buffer,
                websocket=mock_websocket,
            )

        assert ok is True
        assert new_time == recent_time  # unchanged
        mock_send.assert_not_called()

    async def test_sends_heartbeat_after_interval(
        self, mock_buffer, mock_websocket, active_tasks_running
    ) -> None:
        """Should send if interval has elapsed."""
        from main_server import _maybe_send_heartbeat

        old_time = time.monotonic() - HEARTBEAT_INTERVAL - 1  # past interval

        with patch("main_server._safe_ws_send", new_callable=AsyncMock, return_value=True) as mock_send:
            new_time, ok = await _maybe_send_heartbeat(
                last_hb_time=old_time,
                session_id="sess1",
                last_seen=10,
                active_tasks=active_tasks_running,
                buffer=mock_buffer,
                websocket=mock_websocket,
            )

        assert ok is True
        assert new_time > old_time
        mock_send.assert_called_once()

    async def test_reports_dead_agent_when_task_done(
        self, mock_buffer, mock_websocket, active_tasks_done
    ) -> None:
        """Should report agent_alive=False when task is done."""
        from main_server import _maybe_send_heartbeat

        mock_buffer.get_state.return_value = "running"

        sent_data = {}

        async def capture_send(ws: MagicMock, data: dict) -> bool:
            sent_data.update(data)
            return True

        with patch("main_server._safe_ws_send", side_effect=capture_send):
            await _maybe_send_heartbeat(
                last_hb_time=0.0,
                session_id="sess1",
                last_seen=10,
                active_tasks=active_tasks_done,
                buffer=mock_buffer,
                websocket=mock_websocket,
            )

        assert sent_data.get("agent_alive") is False

    async def test_reports_alive_when_buffer_terminal(
        self, mock_buffer, mock_websocket, active_tasks_done
    ) -> None:
        """Should report agent_alive=True when buffer is in terminal state."""
        from main_server import _maybe_send_heartbeat

        mock_buffer.get_state.return_value = "completed"

        sent_data = {}

        async def capture_send(ws: MagicMock, data: dict) -> bool:
            sent_data.update(data)
            return True

        with patch("main_server._safe_ws_send", side_effect=capture_send):
            await _maybe_send_heartbeat(
                last_hb_time=0.0,
                session_id="sess1",
                last_seen=10,
                active_tasks=active_tasks_done,
                buffer=mock_buffer,
                websocket=mock_websocket,
            )

        assert sent_data.get("agent_alive") is True

    async def test_returns_false_on_send_failure(
        self, mock_buffer, mock_websocket, active_tasks_running
    ) -> None:
        """Should return ok=False when WebSocket send fails."""
        from main_server import _maybe_send_heartbeat

        with patch("main_server._safe_ws_send", new_callable=AsyncMock, return_value=False):
            new_time, ok = await _maybe_send_heartbeat(
                last_hb_time=0.0,
                session_id="sess1",
                last_seen=10,
                active_tasks=active_tasks_running,
                buffer=mock_buffer,
                websocket=mock_websocket,
            )

        assert ok is False

    async def test_includes_session_id_and_index(
        self, mock_buffer, mock_websocket, active_tasks_running
    ) -> None:
        """Heartbeat message should include session_id and index."""
        from main_server import _maybe_send_heartbeat

        sent_data = {}

        async def capture_send(ws: MagicMock, data: dict) -> bool:
            sent_data.update(data)
            return True

        with patch("main_server._safe_ws_send", side_effect=capture_send):
            await _maybe_send_heartbeat(
                last_hb_time=0.0,
                session_id="sess_abc",
                last_seen=42,
                active_tasks=active_tasks_running,
                buffer=mock_buffer,
                websocket=mock_websocket,
            )

        assert sent_data.get("session_id") == "sess_abc"
        assert sent_data.get("index") == 42
        assert sent_data.get("type") == "heartbeat"

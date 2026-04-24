"""Integration test: verify the subscribe loop exits gracefully when
the WebSocket closes while the agent task is still running."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestWsSubscribeCrash:
    """When the WebSocket closes while the agent task runs, the subscribe
    loop should exit gracefully without crashing, and the agent task should
    be cancelled (not orphaned)."""

    @pytest.mark.anyio
    async def test_safe_ws_send_returns_false_on_runtime_error(self):
        """_safe_ws_send returns False when the WS raises RuntimeError."""
        from main_server import _safe_ws_send

        mock_ws = MagicMock()
        mock_ws.send_text = AsyncMock(
            side_effect=RuntimeError("websocket.send after websocket.close")
        )

        result = await _safe_ws_send(mock_ws, {"type": "heartbeat"})
        assert result is False

    @pytest.mark.anyio
    async def test_safe_ws_send_returns_true_on_success(self):
        """_safe_ws_send returns True when the WS send succeeds."""
        from main_server import _safe_ws_send

        mock_ws = MagicMock()
        mock_ws.send_text = AsyncMock()

        result = await _safe_ws_send(mock_ws, {"type": "heartbeat", "data": "test"})
        assert result is True
        mock_ws.send_text.assert_called_once()

    @pytest.mark.anyio
    async def test_safe_ws_send_reraises_cancelled_error(self):
        """_safe_ws_send re-raises CancelledError to allow task cancellation."""
        from main_server import _safe_ws_send

        mock_ws = MagicMock()
        mock_ws.send_text = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await _safe_ws_send(mock_ws, {"type": "heartbeat"})

    @pytest.mark.anyio
    async def test_safe_ws_send_returns_false_on_generic_exception(self):
        """_safe_ws_send returns False for any non-CancelledError exception."""
        from main_server import _safe_ws_send

        mock_ws = MagicMock()
        mock_ws.send_text = AsyncMock(side_effect=ValueError("something went wrong"))

        result = await _safe_ws_send(mock_ws, {"type": "heartbeat"})
        assert result is False

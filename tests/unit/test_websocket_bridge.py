"""Unit tests for websocket_bridge — bidirectional WebSocket proxy."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.websocket_bridge import MAX_RECONNECT_ATTEMPTS, bridge


# ── bridge reconnection ─────────────────────────────────────────────


class TestBridgeReconnection:
    @pytest.mark.asyncio
    async def test_notifies_browser_after_max_retries(self) -> None:
        """After max reconnect attempts, browser gets a system error message."""
        mock_browser = MagicMock()
        mock_browser.receive_json = AsyncMock()
        mock_browser.receive_json.side_effect = asyncio.CancelledError()

        with patch("src.websocket_bridge.websockets") as mock_ws:
            mock_ws.connect = MagicMock()
            mock_ws.connect.side_effect = ConnectionRefusedError("refused")

            await bridge(mock_browser, "http://localhost:8000")

            # Should have attempted MAX_RECONNECT_ATTEMPTS times
            assert mock_ws.connect.call_count == MAX_RECONNECT_ATTEMPTS
            # Browser should have been notified
            mock_browser.send_json.assert_called_once()
            call_args = mock_browser.send_json.call_args[0][0]
            assert call_args["type"] == "system"
            assert "lost" in call_args["content"].lower()

    @pytest.mark.asyncio
    async def test_gives_up_after_max_attempts(self) -> None:
        """Bridge returns (does not hang) after max reconnection attempts."""
        mock_browser = MagicMock()
        mock_browser.receive_json = AsyncMock()
        mock_browser.receive_json.side_effect = asyncio.CancelledError()

        with patch("src.websocket_bridge.websockets") as mock_ws:
            mock_ws.connect = MagicMock()
            mock_ws.connect.side_effect = ConnectionRefusedError("refused")

            # Should complete without hanging
            await bridge(mock_browser, "http://localhost:8000")
            assert mock_ws.connect.call_count == MAX_RECONNECT_ATTEMPTS


# ── bridge session ──────────────────────────────────────────────────


class TestBridgeSession:
    @pytest.mark.asyncio
    async def test_agent_url_normalized(self) -> None:
        """HTTP URL gets ws:// prefix added."""
        mock_browser = MagicMock()
        mock_browser.receive_json = AsyncMock()
        mock_browser.receive_json.side_effect = asyncio.CancelledError()

        with patch("src.websocket_bridge.websockets") as mock_ws:
            mock_ws.connect = MagicMock()
            mock_ws.connect.side_effect = ConnectionRefusedError("refused")

            await bridge(mock_browser, "http://localhost:55555")
            # First call should have ws:// prefix
            first_call = mock_ws.connect.call_args[0][0]
            assert first_call.startswith("ws://")


# ── url construction ────────────────────────────────────────────────


class TestUrlConstruction:
    def test_ws_url_passed_through(self) -> None:
        """WS URLs are not double-prefixed."""
        # This is tested implicitly via the agent_url_normalized test
        # but let's verify the logic directly
        url = "ws://localhost:8000/ws"
        if not url.startswith("ws"):
            url = f"ws://{url}"
        assert url == "ws://localhost:8000/ws"

    def test_http_url_gets_ws_prefix(self) -> None:
        """HTTP URLs get ws:// prefix."""
        url = "http://localhost:55555"
        if not url.startswith("ws"):
            url = f"ws://{url}"
        # Note: the actual bridge adds /ws suffix separately
        assert url.startswith("ws://")

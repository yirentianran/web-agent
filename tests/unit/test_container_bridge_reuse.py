"""Tests for container bridge connection reuse.

Covers connect() idempotency, _reset_for_new_run(), _is_connection_alive(),
run_and_stream() guard for dead connection, disconnect(), and stale cancelled
message filtering.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.container_bridge import ContainerBridge


class TestConnectIdempotent:
    """connect() should be a no-op when already connected."""

    def test_connect_noop_when_already_connected(self):
        bridge = ContainerBridge(
            container_url="http://localhost:9999",
            session_id="s1",
            user_id="u1",
            buffer=AsyncMock(),
        )
        bridge._connected = True
        bridge._ws = MagicMock()

        with patch("src.container_bridge.websockets.connect") as mock_ws_connect:
            import asyncio
            asyncio.get_event_loop().run_until_complete(bridge.connect())

        mock_ws_connect.assert_not_called()

    def test_connect_proceeds_when_not_connected(self):
        bridge = ContainerBridge(
            container_url="http://localhost:9999",
            session_id="s1",
            user_id="u1",
            buffer=AsyncMock(),
        )
        assert not bridge._connected

        with patch("src.container_bridge.websockets.connect", new_callable=AsyncMock) as mock_ws_connect:
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_resp.__enter__.return_value = mock_resp
                mock_resp.read.return_value = b'{"status": "ok"}'
                mock_urlopen.return_value = mock_resp

                mock_ws = AsyncMock()
                mock_ws_connect.return_value = mock_ws

                import asyncio
                asyncio.get_event_loop().run_until_complete(bridge.connect())

        mock_ws_connect.assert_called_once()
        assert bridge._connected


class TestIsConnectionAlive:
    """_is_connection_alive() should return True only when connected and have WS."""

    def test_alive_when_connected_with_ws(self):
        bridge = ContainerBridge(
            container_url="http://localhost:9999",
            session_id="s1",
            user_id="u1",
            buffer=AsyncMock(),
        )
        bridge._connected = True
        bridge._ws = MagicMock()
        assert bridge._is_connection_alive() is True

    def test_dead_when_not_connected(self):
        bridge = ContainerBridge(
            container_url="http://localhost:9999",
            session_id="s1",
            user_id="u1",
            buffer=AsyncMock(),
        )
        bridge._connected = False
        bridge._ws = MagicMock()
        assert bridge._is_connection_alive() is False

    def test_dead_when_no_ws(self):
        bridge = ContainerBridge(
            container_url="http://localhost:9999",
            session_id="s1",
            user_id="u1",
            buffer=AsyncMock(),
        )
        bridge._connected = True
        bridge._ws = None
        assert bridge._is_connection_alive() is False


class TestResetForNewRun:
    """_reset_for_new_run() should clear per-run state and drain stale queue items."""

    def test_reset_clears_cancel_event_and_state(self):
        bridge = ContainerBridge(
            container_url="http://localhost:9999",
            session_id="s1",
            user_id="u1",
            buffer=AsyncMock(),
        )
        bridge._cancel_event.set()
        bridge._error = "some error"
        bridge._result = {"type": "result", "duration_ms": 1000}

        bridge._reset_for_new_run()

        assert not bridge._cancel_event.is_set()
        assert bridge._error is None
        assert bridge._result is None

    def test_reset_drains_stale_queue_messages(self):
        bridge = ContainerBridge(
            container_url="http://localhost:9999",
            session_id="s1",
            user_id="u1",
            buffer=AsyncMock(),
        )
        bridge._receive_queue.put_nowait({"type": "cancelled"})
        bridge._receive_queue.put_nowait({"type": "done"})
        bridge._receive_queue.put_nowait({"type": "stream_event"})

        bridge._reset_for_new_run()

        assert bridge._receive_queue.empty()


class TestRunAndStreamGuards:
    """run_and_stream() should raise ConnectionError when connection is dead."""

    @pytest.mark.asyncio
    async def test_raises_connection_error_when_not_connected(self):
        bridge = ContainerBridge(
            container_url="http://localhost:9999",
            session_id="s1",
            user_id="u1",
            buffer=AsyncMock(),
        )
        bridge._connected = False
        bridge._ws = None

        with pytest.raises(ConnectionError, match="not connected"):
            await bridge.run_and_stream("prompt", {})

    @pytest.mark.asyncio
    async def test_raises_connection_error_when_ws_is_none(self):
        bridge = ContainerBridge(
            container_url="http://localhost:9999",
            session_id="s1",
            user_id="u1",
            buffer=AsyncMock(),
        )
        bridge._connected = True
        bridge._ws = None

        with pytest.raises(ConnectionError, match="not connected"):
            await bridge.run_and_stream("prompt", {})


class TestDisconnect:
    """disconnect() should clean up all state."""

    @pytest.mark.asyncio
    async def test_disconnect_sets_connected_false(self):
        bridge = ContainerBridge(
            container_url="http://localhost:9999",
            session_id="s1",
            user_id="u1",
            buffer=AsyncMock(),
        )
        bridge._connected = True
        bridge._ws = AsyncMock()

        await bridge.disconnect()

        assert bridge._connected is False

    @pytest.mark.asyncio
    async def test_disconnect_cancels_receive_task(self):
        bridge = ContainerBridge(
            container_url="http://localhost:9999",
            session_id="s1",
            user_id="u1",
            buffer=AsyncMock(),
        )
        import asyncio
        loop = asyncio.get_event_loop()
        bridge._receive_task = loop.create_task(asyncio.sleep(10))
        bridge._connected = True
        bridge._ws = AsyncMock()

        await bridge.disconnect()

        assert bridge._receive_task is None

    @pytest.mark.asyncio
    async def test_disconnect_closes_ws(self):
        bridge = ContainerBridge(
            container_url="http://localhost:9999",
            session_id="s1",
            user_id="u1",
            buffer=AsyncMock(),
        )
        mock_ws = AsyncMock()
        bridge._ws = mock_ws
        bridge._connected = True

        await bridge.disconnect()

        mock_ws.close.assert_called_once()
        assert bridge._ws is None

    @pytest.mark.asyncio
    async def test_close_is_alias_for_disconnect(self):
        bridge = ContainerBridge(
            container_url="http://localhost:9999",
            session_id="s1",
            user_id="u1",
            buffer=AsyncMock(),
        )
        mock_ws = AsyncMock()
        bridge._ws = mock_ws
        bridge._connected = True

        await bridge.close()

        mock_ws.close.assert_called_once()
        assert bridge._connected is False


class TestRunActiveFlag:
    """_run_active flag should be managed correctly by run_and_stream."""

    @pytest.mark.asyncio
    async def test_run_active_false_after_normal_completion(self):
        bridge = ContainerBridge(
            container_url="http://localhost:9999",
            session_id="s1",
            user_id="u1",
            buffer=AsyncMock(),
        )
        bridge._connected = True
        bridge._ws = AsyncMock()

        # Simulate run_and_stream: send_run then cancel immediately
        # to trigger a short execution
        bridge.send_run = AsyncMock()
        bridge.send_cancel = AsyncMock()
        bridge._receive_task = MagicMock()
        bridge._receive_task.done.return_value = True

        # Queue a "done" message to complete cleanly
        bridge._receive_queue.put_nowait({"type": "done"})

        await bridge.run_and_stream("prompt", {})

        assert bridge._run_active is False

    @pytest.mark.asyncio
    async def test_run_active_false_after_error(self):
        bridge = ContainerBridge(
            container_url="http://localhost:9999",
            session_id="s1",
            user_id="u1",
            buffer=AsyncMock(),
        )
        bridge._connected = True
        bridge._ws = AsyncMock()
        bridge.send_run = AsyncMock()
        bridge._receive_task = MagicMock()
        bridge._receive_task.done.return_value = True

        # Queue an error to trigger error path
        bridge._receive_queue.put_nowait({"type": "error", "message": "fail"})

        await bridge.run_and_stream("prompt", {})

        assert bridge._run_active is False

"""WebSocket bridge from main_server to a container's agent_server.

When ``CONTAINER_MODE=true``, the main_server routes agent tasks through this
bridge instead of creating ``ClaudeSDKClient`` directly in-process.

The bridge:
1. Connects to the container's ``agent_server:app`` WebSocket (typically at
   ``ws://localhost:{ephemeral_port}/ws``)
2. Sends the task (prompt + serialized options) and streams events back into
   the ``MessageBuffer``
3. Handles ``AskUserQuestion`` bidirectionally — the agent inside the container
   sends a ``permission_check``, the bridge buffers the question and registers
   a ``Future`` that the main_server WebSocket handler resolves when the
   browser sends its answer
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger("container_bridge")

# Module-level registry for AskUserQuestion Futures.
# Key: session_id — only one question can be pending per session at a time
# because the agent is blocked waiting for the answer.
bridge_answer_futures: dict[str, asyncio.Future[dict]] = {}


class ContainerBridge:
    """WebSocket client bridging main_server to a container's agent_server."""

    def __init__(
        self,
        container_url: str,
        session_id: str,
        user_id: str,
        buffer,  # MessageBuffer (avoid circular import)
        session_store=None,
    ):
        self.container_url = container_url
        self.session_id = session_id
        self.user_id = user_id
        self.buffer = buffer
        self.session_store = session_store

        self._ws: ClientConnection | None = None
        self._receive_task: asyncio.Task | None = None
        self._receive_queue: asyncio.Queue = asyncio.Queue()
        self._cancel_event: asyncio.Event = asyncio.Event()
        self._error: str | None = None

    async def connect(self, retries: int = 3, backoff: float = 1.0) -> None:
        """Open WebSocket to container's /ws endpoint with retry."""
        ws_url = f"{self.container_url}/ws".replace("http://", "ws://").replace("https://", "wss://")
        last_error = None
        for attempt in range(retries):
            try:
                self._ws = await websockets.connect(
                    ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=10,
                )
                logger.info(
                    "Connected to container %s (attempt %d/%d)",
                    ws_url, attempt + 1, retries,
                )
                return
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Connection attempt %d/%d to %s failed: %s",
                    attempt + 1, retries, ws_url, exc,
                )
                if attempt < retries - 1:
                    await asyncio.sleep(backoff)
        raise ConnectionError(f"Failed to connect to container after {retries} attempts") from last_error

    async def send_run(self, prompt: str, options: dict) -> None:
        """Send the 'run' message to start an agent task."""
        assert self._ws is not None, "Must call connect() first"
        await self._ws.send(json.dumps({
            "type": "run",
            "prompt": prompt,
            "session_id": self.session_id,
            "options": options,
        }))

    async def send_cancel(self) -> None:
        """Send cancel message to the container."""
        self._cancel_event.set()
        if self._ws:
            await self._ws.send(json.dumps({"type": "cancel"}))

    async def send_answer(self, tool_use_id: str, answers: dict) -> None:
        """Forward a browser's answer back to the container."""
        if self._ws:
            await self._ws.send(json.dumps({
                "type": "answer",
                "tool_use_id": tool_use_id,
                "answers": answers,
            }))

    async def run_and_stream(self, prompt: str, options: dict) -> None:
        """Full lifecycle: connect, run, stream events to buffer, handle AskUserQuestion.

        Raises:
            ConnectionError: If unable to connect to the container.
            TimeoutError: If the AskUserQuestion future times out (handled internally).
        """
        await self.connect()

        # Start the receive loop as a background task so cancellation works
        self._receive_task = asyncio.create_task(
            self._receive_loop(),
            name=f"bridge-recv-{self.session_id}",
        )

        await self.send_run(prompt, options)

        try:
            while True:
                # Check cancellation
                if self._cancel_event.is_set():
                    await self.send_cancel()
                    break

                try:
                    data = await asyncio.wait_for(self._receive_queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    # No message in 30s — check if agent is still alive
                    if self._receive_task.done():
                        exc = self._receive_task.exception()
                        if exc:
                            logger.error("Receive task failed: %s", exc)
                        break
                    continue

                msg_type = data.get("type", "")

                if msg_type == "stream_event":
                    event = data["event"]
                    self.buffer.add_message(self.session_id, event, self.user_id)

                elif msg_type == "permission_check":
                    await self._handle_permission_check(data)

                elif msg_type == "done":
                    logger.info("Container task done for session %s", self.session_id)
                    break

                elif msg_type == "error":
                    self._error = data.get("message", "Container agent error")
                    self.buffer.add_message(self.session_id, {
                        "type": "error",
                        "message": self._error,
                    }, self.user_id)
                    break

                elif msg_type == "cancelled":
                    logger.info("Container task cancelled for session %s", self.session_id)
                    break
        finally:
            await self.close()

    async def _receive_loop(self) -> None:
        """Background task that reads raw messages from the container WebSocket."""
        try:
            assert self._ws is not None
            async for raw_msg in self._ws:
                try:
                    data = json.loads(raw_msg)
                    self._receive_queue.put_nowait(data)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from container: %s", raw_msg[:200])
        except ConnectionClosed as exc:
            logger.info("Container WS connection closed: code=%s reason=%s", exc.code, exc.reason)
            if not self._error:
                self._receive_queue.put_nowait({
                    "type": "error",
                    "message": f"Container connection closed: {exc.reason or 'unexpected'}",
                })
        except Exception:
            logger.exception("Receive loop error")
            self._receive_queue.put_nowait({
                "type": "error",
                "message": "Container bridge receive error",
            })

    async def _handle_permission_check(self, data: dict) -> None:
        """Handle an AskUserQuestion permission check from the container agent.

        1. Buffer the question for browser UI display
        2. Register a Future keyed by session_id
        3. Wait for the browser's answer (resolved by handle_ws in main_server)
        4. Forward the answer to the container
        """
        tool_use_id = data.get("tool_use_id", "")
        tool_input = data.get("tool_input", {})

        # Buffer the question for browser display (standard tool_use format)
        self.buffer.add_message(self.session_id, {
            "type": "tool_use",
            "name": "AskUserQuestion",
            "id": tool_use_id,
            "input": tool_input,
        }, self.user_id)

        # Register future for the browser answer
        answer_future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        bridge_answer_futures[self.session_id] = answer_future

        try:
            answer = await asyncio.wait_for(answer_future, timeout=300)
            await self.send_answer(tool_use_id, answer)
        except asyncio.TimeoutError:
            logger.warning("AskUserQuestion timeout for session %s", self.session_id)
            await self.send_answer(tool_use_id, {"error": "timeout"})
        finally:
            bridge_answer_futures.pop(self.session_id, None)

    async def close(self) -> None:
        """Close the WebSocket connection and clean up."""
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        if self._ws:
            await self._ws.close()
            self._ws = None

        # Clean up any lingering answer future
        bridge_answer_futures.pop(self.session_id, None)

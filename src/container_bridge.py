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
import os
import time
import urllib.error
import urllib.request
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger("src.container_bridge")

# Module-level registry for AskUserQuestion Futures.
# Key: session_id — only one question can be pending per session at a time
# because the agent is blocked waiting for the answer.
bridge_answer_futures: dict[str, asyncio.Future[dict]] = {}


def _get_agent_secret() -> str:
    """Return the agent WebSocket auth secret, matching the container's env."""
    from src.container_manager import _agent_secret  # noqa: PLC0415

    return _agent_secret


class ContainerBridge:
    """WebSocket client bridging main_server to a container's agent_server."""

    def __init__(
        self,
        container_url: str,
        session_id: str,
        user_id: str,
        buffer,  # MessageBuffer (avoid circular import)
        session_store=None,
        skill_manager=None,
        ctx=None,  # EventContext
        model=None,  # str | None
        tool_use_names=None,  # dict[str, str]
    ):
        self.container_url = container_url
        self.session_id = session_id
        self.user_id = user_id
        self.buffer = buffer
        self.session_store = session_store
        self.skill_manager = skill_manager

        self.ctx = ctx
        self.model = model
        self.tool_use_names = tool_use_names or {}

        self._ws: ClientConnection | None = None
        self._connected: bool = False
        self._receive_task: asyncio.Task | None = None
        self._receive_queue: asyncio.Queue = asyncio.Queue()
        self._cancel_event: asyncio.Event = asyncio.Event()
        self._error: str | None = None
        self._result: dict[str, Any] | None = None
        self._run_active: bool = False

    def _is_connection_alive(self) -> bool:
        """Return True if the WebSocket connection is established and usable."""
        return self._connected and self._ws is not None

    async def connect(self, retries: int = 3, backoff: float = 1.0) -> None:
        """Open WebSocket to container's /ws endpoint with retry and health check.

        Idempotent: no-op if already connected.
        """
        if self._connected:
            return
        ws_url = f"{self.container_url}/ws".replace("http://", "ws://").replace("https://", "wss://")
        health_url = f"{self.container_url}/api/health"

        # Wait for container health endpoint to be ready (up to 15s)
        deadline = time.time() + 15
        last_health_error = None
        while time.time() < deadline:
            try:
                req = urllib.request.Request(health_url)
                with urllib.request.urlopen(req, timeout=2) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read())
                        if data.get("status") == "ok":
                            break
            except (urllib.error.URLError, OSError, ConnectionError, json.JSONDecodeError) as exc:
                last_health_error = exc
            await asyncio.sleep(0.5)
        else:
            logger.warning(
                "Container health check failed for %s after %.1fs: %s",
                health_url, time.time() - (deadline - 15), last_health_error,
            )

        last_error = None
        for attempt in range(retries):
            try:
                self._ws = await websockets.connect(
                    ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=10,
                    additional_headers={
                        "X-Agent-Token": _get_agent_secret(),
                    },
                )
                self._connected = True
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

    def _reset_for_new_run(self) -> None:
        """Clear per-run state and drain stale messages from the receive queue."""
        self._cancel_event.clear()
        self._error = None
        self._result = None
        while not self._receive_queue.empty():
            try:
                self._receive_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

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
        """Run a task over the existing WebSocket connection.

        ``connect()`` must be called once before the first call to this method.
        Subsequent calls reuse the same connection.

        Raises:
            ConnectionError: If the connection is dead (caller should reconnect).
            TimeoutError: If the AskUserQuestion future times out (handled internally).
        """
        if not self._is_connection_alive():
            raise ConnectionError(
                f"Container bridge for session {self.session_id} is not connected"
            )

        self._reset_for_new_run()
        self._run_active = True

        # Start the receive loop as a background task (only once per connection)
        if self._receive_task is None or self._receive_task.done():
            self._receive_task = asyncio.create_task(
                self._receive_loop(),
                name=f"bridge-recv-{self.session_id}",
            )

        t_send = time.monotonic()
        await self.send_run(prompt, options)
        t_sent = time.monotonic()
        logger.info(
            "[LATENCY] session=%s send_run=%.1fms",
            self.session_id,
            (t_sent - t_send) * 1000,
        )

        accumulated_text = ""
        explicit_assistant = False
        first_event = True
        event_count = 0
        t_first_content = None

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
                        error_msg = f"Container receive task died: {exc}" if exc else "Container receive task ended unexpectedly"
                        logger.error("Receive task failed for session %s: %s", self.session_id, exc)
                        if not self._error:
                            await self.buffer.add_message(self.session_id, {
                                "type": "error",
                                "message": error_msg,
                            }, self.user_id)
                            self._error = error_msg
                        break
                    continue

                msg_type = data.get("type", "")

                if first_event:
                    first_event = False
                    t_first = time.monotonic()
                    logger.info(
                        "[LATENCY] session=%s first_event=%s roundtrip_since_send=%.0fms",
                        self.session_id,
                        msg_type,
                        (t_first - t_sent) * 1000,
                    )

                # Touch user activity to prevent container idle timeout
                try:
                    from src.container_manager import touch_user as _touch  # noqa: PLC0415
                    _touch(self.user_id)
                except ImportError:
                    pass

                # ── Container latency logs forwarded via WS ──
                if msg_type == "latency_log":
                    logger.info(
                        "[CONTAINER_LATENCY] session=%s %s",
                        self.session_id,
                        data.get("message", ""),
                    )
                    continue

                # ── Bridge-specific: bidirectional AskUserQuestion ──
                if msg_type == "permission_check":
                    await self._handle_permission_check(data)
                    continue

                # ── Terminal signals ──
                if msg_type == "done":
                    logger.info("Container task done for session %s", self.session_id)
                    if accumulated_text.strip() and not explicit_assistant:
                        logger.info(
                            "Bridge emitting synthetic assistant message len=%d for session %s",
                            len(accumulated_text), self.session_id,
                        )
                        await self.buffer.add_message(self.session_id, {
                            "type": "assistant",
                            "content": accumulated_text,
                        }, self.user_id)
                    break

                if msg_type == "error":
                    raw_msg = data.get("message", "")
                    self._error = raw_msg or "Container agent error (empty message)"
                    logger.error(
                        "Container error for session %s: message=%r",
                        self.session_id, raw_msg,
                    )
                    await self.buffer.add_message(self.session_id, {
                        "type": "error",
                        "message": self._error,
                    }, self.user_id)
                    break

                if msg_type == "cancelled":
                    if not self._run_active:
                        # Stale cancellation from a previous run — ignore
                        continue
                    logger.info("Container task cancelled for session %s", self.session_id)
                    break

                # ── Delegate everything else to shared pipeline ──
                # Lazy import to avoid circular import (main_server imports ContainerBridge)
                from main_server import message_to_dicts  # noqa: PLC0415
                from src.event_pipeline import process_event  # noqa: PLC0415

                for event in message_to_dicts(
                    data, model=self.model, tool_use_names=self.tool_use_names,
                ):
                    event_count += 1
                    if event.get("type") == "result":
                        self._result = event
                        continue

                    # Track streaming text for synthetic assistant fallback
                    if event["type"] == "assistant":
                        explicit_assistant = True
                        if t_first_content is None:
                            t_first_content = time.monotonic()
                    elif event["type"] == "stream_event":
                        inner = event.get("event", {})
                        if inner.get("type") == "content_block_delta":
                            delta = inner.get("delta", {})
                            if isinstance(delta, dict) and delta.get("type") == "text_delta":
                                accumulated_text += delta.get("text", "")
                                if t_first_content is None:
                                    t_first_content = time.monotonic()

                    if self.ctx is not None:
                        await process_event(self.ctx, event)

            t_done = time.monotonic()
            logger.info(
                "[LATENCY] session=%s event_loop_done events=%d first_content=%.0fms total_loop=%.0fms",
                self.session_id,
                event_count,
                (t_first_content - t_sent) * 1000 if t_first_content else -1,
                (t_done - t_sent) * 1000,
            )
        finally:
            self._run_active = False

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
            logger.warning(
                "Container WS closed for session %s: code=%s reason=%s",
                self.session_id, exc.code, exc.reason,
            )
            if not self._error:
                error_msg = f"Container connection closed unexpectedly (code={exc.code})"
                if exc.reason:
                    error_msg += f": {exc.reason}"
                self._receive_queue.put_nowait({
                    "type": "error",
                    "message": error_msg,
                })
        except Exception:
            logger.exception("Receive loop error for session %s", self.session_id)
            self._receive_queue.put_nowait({
                "type": "error",
                "message": f"Container bridge receive error for session {self.session_id}",
            })

    async def _handle_permission_check(self, data: dict) -> None:
        """Handle an AskUserQuestion permission check from the container agent.

        1. Buffer the question for browser UI display
        2. Register a Future keyed by session_id
        3. Wait for the browser's answer (resolved by handle_ws in main_server)
        4. Forward the answer to the container

        NOTE: Direct buffer write instead of process_event() because
        process_event explicitly skips AskUserQuestion tool_use events
        (the skip rule delegates buffering to this handler).
        """
        tool_use_id = data.get("tool_use_id", "")
        tool_input = data.get("tool_input", {})

        # Buffer the question for browser display (standard tool_use format)
        await self.buffer.add_message(self.session_id, {
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

    async def disconnect(self) -> None:
        """Close the WebSocket connection and clean up all resources."""
        self._connected = False
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except (asyncio.CancelledError, Exception):
                pass
        self._receive_task = None

        if self._ws:
            await self._ws.close()
            self._ws = None

        # Clean up any lingering answer future
        bridge_answer_futures.pop(self.session_id, None)

    async def close(self) -> None:
        """Alias for disconnect() — backward compatibility."""
        await self.disconnect()

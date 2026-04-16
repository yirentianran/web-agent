"""Bidirectional WebSocket bridge between browser and agent_server.

In container mode, main_server proxies messages:
  browser <---> main_server <---> container's agent_server
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import websockets

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = logging.getLogger(__name__)

MAX_RECONNECT_ATTEMPTS = 5
MAX_BACKOFF_SECONDS = 30
INITIAL_BACKOFF_SECONDS = 1


async def bridge(browser_ws: "WebSocket", agent_url: str) -> None:
    """Bridge messages between browser WebSocket and agent_server WebSocket.

    Implements exponential backoff reconnection to agent_server.
    Returns when either side disconnects permanently.
    """
    agent_uri = agent_url
    if agent_url.startswith("http://"):
        agent_uri = agent_url.replace("http://", "ws://", 1) + "/ws"
    elif agent_url.startswith("https://"):
        agent_uri = agent_url.replace("https://", "wss://", 1) + "/ws"
    elif not agent_url.startswith("ws"):
        agent_uri = f"ws://{agent_url}/ws"

    for attempt in range(MAX_RECONNECT_ATTEMPTS):
        try:
            await _bridge_session(browser_ws, agent_uri)
        except asyncio.CancelledError:
            raise
        except Exception:
            if attempt >= MAX_RECONNECT_ATTEMPTS - 1:
                logger.error(
                    "Agent connection lost after %d attempts, giving up",
                    MAX_RECONNECT_ATTEMPTS,
                )
                try:
                    await browser_ws.send_json({
                        "type": "system",
                        "content": "Agent connection lost. Please reconnect.",
                    })
                except Exception:
                    pass  # browser already disconnected
                return

            delay = min(
                INITIAL_BACKOFF_SECONDS * (2**attempt),
                MAX_BACKOFF_SECONDS,
            )
            logger.warning(
                "Agent connection lost, reconnecting in %.1fs (attempt %d/%d)",
                delay,
                attempt + 1,
                MAX_RECONNECT_ATTEMPTS,
            )
            await asyncio.sleep(delay)


async def _bridge_session(browser_ws: "WebSocket", agent_uri: str) -> None:
    """Single bridge session. Raises on disconnect to trigger reconnect."""
    async with websockets.connect(agent_uri) as agent_ws:
        logger.info("Bridge connected to agent at %s", agent_uri)

        # Create two tasks: browser->agent and agent->browser
        async def browser_to_agent() -> None:
            """Forward messages from browser to agent."""
            try:
                while True:
                    msg = await browser_ws.receive_json()
                    await agent_ws.send(json.dumps(msg))
            except Exception:
                pass  # disconnect triggers outer cleanup

        async def agent_to_browser() -> None:
            """Forward messages from agent to browser."""
            try:
                async for raw_msg in agent_ws:
                    data = json.loads(raw_msg)
                    await browser_ws.send_json(data)
            except Exception:
                pass  # disconnect triggers outer cleanup

        browser_task = asyncio.create_task(browser_to_agent())
        agent_task = asyncio.create_task(agent_to_browser())

        try:
            # Wait for either task to complete (i.e., disconnect)
            done, pending = await asyncio.wait(
                {browser_task, agent_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Cancel the other task
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        finally:
            # Ensure both tasks are cleaned up
            for task in (browser_task, agent_task):
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

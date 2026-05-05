"""Agent server — runs inside each user's isolated Docker container.

Exposes a minimal REST + WebSocket API that the main_server bridges to.
Receives agent task instructions, runs the Claude Agent SDK, streams results back.

The main_server (container_manager.py) mounts per-user volumes:
  /home/agent/.claude/shared-skills   (ro) — shared skill library
  /home/agent/.claude/personal-skills (rw) — user's own skills
  /home/agent/.claude                 (rw) — sessions, memory, settings
  /workspace                          (rw) — file workspace
  /hooks                              (ro) — PreToolUse/PostToolUse/Stop hook scripts
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookContext,
    HookInput,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

logger = logging.getLogger("agent_server")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="agent-server")

WORKSPACE = Path(os.getenv("WORKSPACE", "/workspace"))
CLAUDE_DIR = Path(os.getenv("HOME", "/home/agent")) / ".claude"


@app.get("/api/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "user_id": os.getenv("USER_ID", "unknown")})


@app.websocket("/ws")
async def agent_ws(websocket: WebSocket) -> None:
    """WebSocket endpoint for running agent tasks.

    Receives JSON messages:
      { "type": "run", "prompt": "...", "session_id": "...", "options": {...} }
      { "type": "cancel" }

    Sends JSON messages:
      { "type": "stream_event", "event": {...} }
      { "type": "result", "result": {...} }
      { "type": "error", "message": "..." }
    """
    await websocket.accept()
    logger.info("Agent WebSocket connected")

    client: ClaudeSDKClient | None = None

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type", "")

            if msg_type == "run":
                prompt = msg.get("prompt", "")
                session_id = msg.get("session_id", "")
                options_dict = msg.get("options", {})

                options = ClaudeAgentOptions(
                    system_prompt=options_dict.get("system_prompt", ""),
                    max_turns=options_dict.get("max_turns", 200),
                    permission_mode=options_dict.get("permission_mode", "default"),
                    allowed_tools=options_dict.get("allowed_tools", []),
                    disallowed_tools=options_dict.get("disallowed_tools", []),
                    cwd=str(WORKSPACE),
                    skills_dirs=[
                        str(CLAUDE_DIR / "shared-skills"),
                        str(CLAUDE_DIR / "personal-skills"),
                    ],
                )

                client = ClaudeSDKClient()

                try:
                    async with client.connect(
                        session_id=session_id,
                        options=options,
                    ) as stream:
                        async for event in stream:
                            if isinstance(event, UserMessage):
                                # Send user message into the stream
                                await stream.send_message(prompt)
                            elif isinstance(event, StreamEvent):
                                await websocket.send_json({
                                    "type": "stream_event",
                                    "event": _serialize_event(event),
                                })
                except Exception as exc:
                    logger.error("Agent task error: %s", exc)
                    await websocket.send_json({
                        "type": "error",
                        "message": str(exc),
                    })

                await websocket.send_json({"type": "done"})

            elif msg_type == "cancel":
                if client:
                    await client.cancel()
                await websocket.send_json({"type": "cancelled"})

    except WebSocketDisconnect:
        logger.info("Agent WebSocket disconnected")
    except Exception:
        logger.exception("Agent WebSocket error")
        try:
            await websocket.send_json({
                "type": "error",
                "message": "Internal agent server error",
            })
        except Exception:
            pass


def _serialize_event(event: StreamEvent) -> dict:
    """Extract serializable fields from a StreamEvent for JSON transmission."""
    data: dict = {"type": type(event).__name__}
    if hasattr(event, "message"):
        msg = event.message
        if hasattr(msg, "content"):
            content = msg.content
            if isinstance(content, list):
                blocks = []
                for block in content:
                    if hasattr(block, "text"):
                        blocks.append({"type": "text", "text": block.text})
                    elif hasattr(block, "thinking"):
                        blocks.append({"type": "thinking", "thinking": block.thinking})
                    elif hasattr(block, "name"):  # ToolUseBlock
                        blocks.append({
                            "type": "tool_use",
                            "id": getattr(block, "id", ""),
                            "name": block.name,
                            "input": getattr(block, "input", {}),
                        })
                    elif hasattr(block, "content"):  # ToolResultBlock
                        blocks.append({
                            "type": "tool_result",
                            "tool_use_id": getattr(block, "tool_use_id", ""),
                            "content": block.content,
                        })
                data["blocks"] = blocks
    if hasattr(event, "result"):
        data["result"] = str(event.result)
    return data

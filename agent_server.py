"""Agent server — runs inside each user's isolated Docker container.

Exposes a WebSocket API that the main_server bridges to.
Receives agent task instructions, runs the Claude Agent SDK, streams results back.

The main_server (container_manager.py) mounts per-user volumes:
  /home/agent/.claude/shared-skills   (ro) — shared skill library
  /home/agent/.claude/personal-skills (rw) — user's own skills
  /home/agent/.claude                 (rw) — sessions, memory, settings
  /workspace                          (rw) — file workspace
  /hooks                              (ro) — PreToolUse/PostToolUse/Stop hook scripts
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid as uuid_mod
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
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from src.workspace_enforcement import (
    ContainerPaths,
    _rewrite_bash_command,
    check_bash_command_for_external_writes,
    is_path_within_user_dir,
    rewrite_path_to_workspace,
)

logger = logging.getLogger("agent_server")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="agent-server")

WORKSPACE = Path(os.getenv("WORKSPACE", "/workspace"))
HOME_DIR = Path(os.getenv("HOME", "/home/agent"))
CLAUDE_DIR = HOME_DIR / ".claude"


@app.get("/api/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "user_id": os.getenv("USER_ID", "unknown")})


@app.websocket("/ws")
async def agent_ws(websocket: WebSocket) -> None:
    """WebSocket endpoint for running agent tasks.

    Receives JSON messages:
      {"type": "run", "prompt": "...", "session_id": "...", "options": {...}}
      {"type": "cancel"}
      {"type": "answer", "tool_use_id": "...", "answers": {...}}

    Sends JSON messages:
      {"type": "stream_event", "event": {...}}
      {"type": "permission_check", "tool_use_id": "...", "tool_input": {...}}
      {"type": "done"}
      {"type": "error", "message": "..."}
    """
    await websocket.accept()
    logger.info("Agent WebSocket connected")

    client: ClaudeSDKClient | None = None
    cancel_event: asyncio.Event = asyncio.Event()
    pending_answers: dict[str, asyncio.Future[dict]] = {}

    # ── build path context for this container ─────────────────────
    container_paths = ContainerPaths(workspace=WORKSPACE, home_dir=HOME_DIR)

    # ── PreToolUse hooks ──────────────────────────────────────────
    async def write_path_hook(
        hook_input: HookInput,
        _tool_use_id: str | None,
        _context: HookContext,
    ) -> dict:
        tool_inp = hook_input.get("tool_input", {})
        file_path = str(tool_inp.get("file_path", ""))
        if not file_path:
            return {"sync": True, "continue_": True}
        if is_path_within_user_dir(file_path, container_paths):
            return {"sync": True, "continue_": True}
        rewritten = rewrite_path_to_workspace(file_path, container_paths)
        if rewritten == file_path:
            return {"sync": True, "continue_": True}
        logger.info("PreToolUse[Write]: '%s' -> '%s'", file_path, rewritten)
        new_input = dict(tool_inp)
        new_input["file_path"] = rewritten
        return {
            "sync": True,
            "continue_": True,
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": new_input,
            },
        }

    async def bash_path_hook(
        hook_input: HookInput,
        _tool_use_id: str | None,
        _context: HookContext,
    ) -> dict:
        tool_inp = hook_input.get("tool_input", {})
        cmd = str(tool_inp.get("command", ""))
        if not cmd:
            return {"sync": True, "continue_": True}
        rewritten = _rewrite_bash_command(cmd, container_paths)
        if rewritten == cmd:
            return {"sync": True, "continue_": True}
        logger.info("PreToolUse[Bash]: rewriting command")
        new_input = dict(tool_inp)
        new_input["command"] = rewritten
        return {
            "sync": True,
            "continue_": True,
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": new_input,
            },
        }

    hooks: dict[str, list[HookMatcher]] = {
        "PreToolUse": [
            HookMatcher(matcher="Write", hooks=[write_path_hook]),
            HookMatcher(matcher="Bash", hooks=[bash_path_hook]),
        ],
    }

    # ── can_use_tool callback ─────────────────────────────────────
    async def can_use_tool_cb(
        tool_name: str,
        tool_input: dict,
        ctx: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        if tool_name in ("WebSearch", "WebFetch"):
            return PermissionResultDeny(
                message="WebSearch/WebFetch are disabled in this environment. "
                "Use the MCP fetch tools for web content retrieval."
            )
        if tool_name == "Write":
            file_path = str(tool_input.get("file_path", ""))
            if file_path and not is_path_within_user_dir(file_path, container_paths):
                return PermissionResultDeny(
                    message=f"Write denied: '{file_path}' is outside the user directory. "
                    "Save files within /workspace/ or /home/agent/."
                )
        if tool_name == "Bash":
            cmd = str(tool_input.get("command", ""))
            error = check_bash_command_for_external_writes(cmd, container_paths)
            if error:
                return PermissionResultDeny(message=error)
        if tool_name == "AskUserQuestion":
            return await _handle_ask_user_question(tool_input, websocket, pending_answers)
        return PermissionResultAllow(behavior="allow")

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type", "")

            if msg_type == "answer":
                tool_use_id = msg.get("tool_use_id", "")
                answers = msg.get("answers", {})
                future = pending_answers.get(tool_use_id)
                if future and not future.done():
                    future.set_result(answers)
                continue

            if msg_type == "run":
                prompt = msg.get("prompt", "")
                session_id = msg.get("session_id", "")
                options_dict = msg.get("options", {})

                options = ClaudeAgentOptions(
                    model=options_dict.get("model", "claude-sonnet-4-6"),
                    system_prompt=options_dict.get("system_prompt", ""),
                    max_turns=options_dict.get("max_turns", 200),
                    permission_mode=options_dict.get("permission_mode", "acceptEdits"),
                    allowed_tools=options_dict.get("allowed_tools", []),
                    disallowed_tools=options_dict.get("disallowed_tools", []),
                    cwd=options_dict.get("cwd", str(WORKSPACE)),
                    skills_dirs=options_dict.get("skills_dirs") or [
                        str(CLAUDE_DIR / "shared-skills"),
                        str(CLAUDE_DIR / "personal-skills"),
                    ],
                    mcp_servers=options_dict.get("mcp_servers") or None,
                    include_partial_messages=options_dict.get("include_partial_messages", True),
                    env=options_dict.get("env") or None,
                    resume=options_dict.get("resume_session_id") or None,
                    max_buffer_size=options_dict.get("max_buffer_size", 10 * 1024 * 1024),
                    hooks=hooks,
                    can_use_tool=can_use_tool_cb,
                )

                client = ClaudeSDKClient()
                cancel_event.clear()

                try:
                    async with client.connect(
                        session_id=session_id,
                        options=options,
                    ) as stream:
                        async for event in stream:
                            # Check for cancellation
                            if cancel_event.is_set():
                                await client.cancel()
                                await websocket.send_json({"type": "cancelled"})
                                break

                            if isinstance(event, UserMessage):
                                await stream.send_message(prompt)
                            elif isinstance(event, StreamEvent):
                                await websocket.send_json({
                                    "type": "stream_event",
                                    "event": _serialize_stream_event(event),
                                })
                except Exception as exc:
                    logger.error("Agent task error: %s", exc)
                    await websocket.send_json({
                        "type": "error",
                        "message": str(exc),
                    })
                    continue

                await websocket.send_json({"type": "done"})

            elif msg_type == "cancel":
                cancel_event.set()
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


# ── Helpers ──────────────────────────────────────────────────────────


async def _handle_ask_user_question(
    tool_input: dict,
    websocket: WebSocket,
    pending_answers: dict[str, asyncio.Future[dict]],
) -> PermissionResultAllow:
    """Send AskUserQuestion to main_server bridge, wait for user answer."""
    tool_use_id = f"ask_{uuid_mod.uuid4().hex[:8]}"
    await websocket.send_json({
        "type": "permission_check",
        "tool_use_id": tool_use_id,
        "tool_name": "AskUserQuestion",
        "tool_input": tool_input,
    })
    answer_future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
    pending_answers[tool_use_id] = answer_future
    try:
        answer = await asyncio.wait_for(answer_future, timeout=300)
        return PermissionResultAllow(
            behavior="allow",
            updated_input={"answers": answer},
        )
    except asyncio.TimeoutError:
        return PermissionResultAllow(
            behavior="allow",
            updated_input={"answers": {"error": "timeout"}},
        )
    finally:
        pending_answers.pop(tool_use_id, None)


def _serialize_stream_event(event: StreamEvent) -> dict:
    """Serialize a StreamEvent for JSON transmission to main_server bridge.

    The output format matches ``message_to_dicts`` in main_server.py, ensuring
    the bridge can drop these directly into ``buffer.add_message()``.
    """
    return {
        "type": "stream_event",
        "uuid": event.uuid,
        "event": event.event,
        "session_id": event.session_id,
        "index": event.event.get("index") if event.event and "index" in event.event else None,
    }

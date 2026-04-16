"""Container-internal FastAPI server — the agent execution engine.

Runs inside each user's Docker container. Provides:
- WebSocket endpoint for the main server to bridge to
- Claude Agent SDK integration (subprocess to `claude` CLI)
- Skill loading (shared + personal)
- System prompt building (with memory injection)
- MCP config injection
- Message streaming via AsyncGenerator
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    UserMessage,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Agent Server")

# Global task tracking
active_tasks: dict[str, asyncio.Task] = {}
pending_answers: dict[str, asyncio.Future] = {}

# Message buffer — imported from shared module
from src.message_buffer import MessageBuffer

buffer = MessageBuffer()


# ── Skill loading ─────────────────────────────────────────────────


def load_skills() -> dict[str, dict[str, Any]]:
    """Load all Skills from shared (ro) + personal (rw) directories."""
    skills_dirs = os.getenv(
        "CLAUDE_SKILLS_DIRS",
        "/home/agent/.claude/shared-skills,/home/agent/.claude/personal-skills",
    ).split(",")

    all_skills: dict[str, dict[str, Any]] = {}
    for skills_dir in skills_dirs:
        path = Path(skills_dir)
        if not path.exists():
            continue
        for skill_dir in sorted(path.iterdir()):
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                name = skill_dir.name
                # Personal overrides shared
                all_skills[name] = {
                    "path": str(skill_dir),
                    "source": "shared" if "shared" in str(skill_dir) else "personal",
                    "content": skill_file.read_text(),
                }
    return all_skills


# ── Memory loading ────────────────────────────────────────────────


def load_memory() -> str:
    """Load L1 platform memory from memory.json (on host volume)."""
    mem_file = Path("/home/agent/.claude/memory.json")
    if not mem_file.exists():
        return ""
    try:
        data = json.loads(mem_file.read_text())
    except (json.JSONDecodeError, OSError):
        return ""

    parts: list[str] = []

    entity = data.get("entity_memory", {})
    if entity:
        parts.append("## Enterprise Information\n")
        for key, val in entity.items():
            if val:
                parts.append(f"- {key}: {val}\n")

    audit_ctx = data.get("audit_context", {})
    findings = audit_ctx.get("prior_findings", [])
    if findings:
        parts.append("\n## Prior Audit Findings\n")
        for i, f in enumerate(findings, 1):
            parts.append(f"{i}. {f.get('item', '')} ({f.get('standard', '')}, status: {f.get('status', '')})\n")

    risk = audit_ctx.get("risk_areas", [])
    if risk:
        parts.append(f"\n## Key Risk Areas: {', '.join(risk)}\n")

    files = data.get("file_memory", [])
    if files:
        parts.append("\n## Frequently Used Files\n")
        for f in files:
            parts.append(f"- {f.get('filename', '')} (last used: {f.get('last_used', '')})\n")

    return "\n".join(parts)


# ── System prompt builder ─────────────────────────────────────────


def build_system_prompt(skills: dict[str, dict[str, Any]]) -> str:
    """Assemble the full system prompt from skills + memory."""
    parts = ["You are an expert financial audit assistant."]

    if skills:
        parts.append("\n## Available Skills\n")
        for name, info in skills.items():
            parts.append(f"- {name}: {info.get('source', 'unknown')}\n")

    memory_context = load_memory()
    if memory_context:
        parts.append(f"\n## Memory Context\n\n{memory_context}")

    return "\n".join(parts)


# ── MCP config ────────────────────────────────────────────────────


def load_mcp_config() -> dict[str, Any]:
    """Load MCP server configuration from environment."""
    mcp_config_json = os.getenv("MCP_CONFIG_JSON", "{}")
    try:
        return json.loads(mcp_config_json)
    except json.JSONDecodeError:
        return {}


def load_user_settings() -> dict[str, Any]:
    """Load user preferences from settings.json or environment."""
    settings_file = Path("/home/agent/.claude/settings.json")
    if settings_file.exists():
        try:
            return json.loads(settings_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "model": os.getenv("MODEL", "claude-sonnet-4-6"),
        "max_turns": int(os.getenv("MAX_TURNS", "30")),
        "max_budget_usd": float(os.getenv("MAX_BUDGET_USD", "2.0")),
        "effort": os.getenv("EFFORT", "high"),
    }


# ── SDK integration ──────────────────────────────────────────────

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResult,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolPermissionContext,
    ToolUseBlock,
    UserMessage,
)


def build_sdk_options(
    session_id: str | None = None,
    can_use_tool_callback=None,
) -> ClaudeAgentOptions:
    """Build ClaudeAgentOptions with full configuration."""
    skills = load_skills()
    mcp_config = load_mcp_config()
    settings = load_user_settings()
    max_turns = settings.get("max_turns", 30)

    allowed_tools = [
        "Read", "Edit", "Write", "Glob", "Grep", "Bash",
        "WebFetch", "WebSearch", "Agent", "Skill",
    ]
    for server_name in mcp_config.get("mcpServers", {}):
        cfg = mcp_config["mcpServers"][server_name]
        for tool_name in cfg.get("enabled_tools", []):
            allowed_tools.append(f"mcp__{server_name}__{tool_name}")

    # Build MCP servers dict in SDK format
    mcp_servers: dict[str, Any] = {}
    for server_name, cfg in mcp_config.get("mcpServers", {}).items():
        if cfg.get("type") == "stdio":
            mcp_servers[server_name] = {
                "type": "stdio",
                "command": cfg.get("command", ""),
                "args": cfg.get("args", []),
                "env": cfg.get("env", {}),
            }
        elif cfg.get("type") == "http":
            mcp_servers[server_name] = {
                "type": "http",
                "url": cfg["url"],
            }

    return ClaudeAgentOptions(
        model=settings.get("model", "claude-sonnet-4-6"),
        cwd="/workspace",
        system_prompt=build_system_prompt(skills),
        allowed_tools=allowed_tools,
        max_turns=max_turns,
        permission_mode="bypassPermissions",
        resume=session_id,
        mcp_servers=mcp_servers if mcp_servers else None,
        can_use_tool=can_use_tool_callback,
    )


def message_to_dict(msg) -> dict[str, Any]:
    """Convert an SDK Message dataclass to a serializable dict."""
    if isinstance(msg, UserMessage):
        content = msg.content
        if isinstance(content, list):
            text = " ".join(b.text for b in content if isinstance(b, TextBlock))
        else:
            text = content
        return {"type": "user", "content": text}

    if isinstance(msg, AssistantMessage):
        text_parts = []
        for block in msg.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ThinkingBlock):
                text_parts.append(f"[thinking] {block.thinking}[/thinking]")
            elif isinstance(block, ToolUseBlock):
                return {
                    "type": "tool_use",
                    "name": block.name,
                    "id": block.id,
                    "input": block.input,
                }
        return {"type": "assistant", "content": "\n".join(text_parts)}

    if isinstance(msg, ResultMessage):
        return {
            "type": "result",
            "subtype": msg.subtype,
            "duration_ms": msg.duration_ms,
            "total_cost_usd": msg.total_cost_usd,
            "usage": msg.usage,
            "session_id": msg.session_id,
        }

    # Fallback: try to serialize as dict
    if hasattr(msg, "__dict__"):
        return msg.__dict__
    return {"type": "unknown", "raw": str(msg)}


async def _can_use_tool_for_session(
    session_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    context: ToolPermissionContext,
) -> PermissionResult:
    """Intercept AskUserQuestion and route answer through WebSocket."""
    if tool_name == "AskUserQuestion":
        buffer.add_message(session_id, {
            "type": "tool_use",
            "name": "AskUserQuestion",
            "id": f"ask_{__import__('uuid').uuid4().hex[:8]}",
            "input": tool_input,
        })

        answer_future = asyncio.get_event_loop().create_future()
        pending_answers[session_id] = answer_future
        try:
            answer = await asyncio.wait_for(answer_future, timeout=300)
            return PermissionResultAllow(
                behavior="allow",
                updated_input={"answer": answer},
            )
        except asyncio.TimeoutError:
            return PermissionResultAllow(
                behavior="allow",
                updated_input={"answer": {"error": "timeout"}},
            )
        finally:
            pending_answers.pop(session_id, None)

    return PermissionResultAllow(behavior="allow")


def message_to_dict(msg: Any) -> dict[str, Any]:
    """Convert a Claude SDK Message dataclass to a dict for the message buffer."""
    if isinstance(msg, UserMessage):
        content = msg.content
        if isinstance(content, list):
            content = "".join(getattr(b, "text", str(b)) for b in content)
        return {"type": "user", "content": content}
    if isinstance(msg, AssistantMessage):
        text_parts = []
        for block in msg.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)
            elif hasattr(block, "input"):
                text_parts.append(f"[Tool: {getattr(block, 'name', 'unknown')}]")
            else:
                text_parts.append(str(block))
        return {"type": "assistant", "content": "".join(text_parts)}
    if isinstance(msg, ResultMessage):
        result = {
            "type": "result",
            "subtype": msg.subtype,
            "duration_ms": msg.duration_ms,
            "num_turns": msg.num_turns,
            "is_error": msg.is_error,
        }
        if msg.total_cost_usd is not None:
            result["total_cost_usd"] = msg.total_cost_usd
        if msg.usage:
            result["usage"] = msg.usage
        if msg.result:
            result["content"] = msg.result
        return result
    if isinstance(msg, SystemMessage):
        result = {"type": "system", "subtype": msg.subtype}
        if msg.data:
            result.update(msg.data)
        if isinstance(msg, TaskProgressMessage) and msg.usage:
            result["cost_usd"] = msg.usage.get("total_cost_usd", 0)
        if isinstance(msg, TaskNotificationMessage):
            result["status"] = msg.status
            result["summary"] = msg.summary
        return result
    if hasattr(msg, "__dict__"):
        return {"type": "unknown", "data": msg.__dict__}
    return {"type": "unknown", "content": str(msg)}


async def run_agent_task(session_id: str, user_message: str) -> None:
    """Run the agent using ClaudeSDKClient for bidirectional interaction."""
    import uuid

    async def can_use_tool_cb(
        tool_name: str,
        tool_input: dict[str, Any],
        ctx: ToolPermissionContext,
    ) -> PermissionResult:
        return await _can_use_tool_for_session(
            session_id, tool_name, tool_input, ctx
        )

    options = build_sdk_options(session_id, can_use_tool_callback=can_use_tool_cb)
    client = ClaudeSDKClient(options)

    try:
        await client.connect()
        await client.query(user_message, session_id=session_id)

        async for msg in client.receive_response():
            event = message_to_dict(msg)
            buffer.add_message(session_id, event)

        buffer.mark_done(session_id)
        buffer.add_message(session_id, {
            "type": "system",
            "subtype": "session_state_changed",
            "state": "completed",
        })
    except asyncio.CancelledError:
        buffer.add_message(session_id, {
            "type": "system",
            "subtype": "session_cancelled",
            "message": "Agent task has been cancelled.",
        })
        buffer.mark_done(session_id)
    except Exception as e:
        logger.exception("Agent task failed for session %s", session_id)
        buffer.add_message(session_id, {
            "type": "error",
            "message": str(e),
        })
        buffer.mark_done(session_id)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


# ── WebSocket endpoint ────────────────────────────────────────────


@app.websocket("/ws")
async def handle_agent(websocket: WebSocket) -> None:
    await websocket.accept()

    try:
        while True:
            msg = await websocket.receive_text()
            data = json.loads(msg)

            user_id = data.get("user_id", "default")
            user_message = data.get("message", "")
            session_id = data.get("session_id")
            last_index = data.get("last_index", 0)

            # Handle answer to AskUserQuestion
            if data.get("type") == "answer":
                session_id = data["session_id"]
                answer = data.get("answers", {})
                future = pending_answers.get(session_id)
                if future and not future.done():
                    future.set_result(answer)
                continue

            if not session_id:
                session_id = f"session_{user_id}_{__import__('time').time()}_{__import__('uuid').uuid4().hex[:8]}"

            # Send historical messages (reconnection recovery)
            history = buffer.get_history(session_id, after_index=last_index)
            for i, h in enumerate(history):
                await websocket.send_text(json.dumps({
                    **h,
                    "index": last_index + i,
                    "replay": True,
                }))

            # Start or reuse agent task
            task_key = f"task_{session_id}"
            if task_key not in active_tasks or active_tasks[task_key].done():
                task = asyncio.create_task(run_agent_task(session_id, user_message))
                active_tasks[task_key] = task

            # Subscribe to real-time messages
            last_seen = last_index + len(history)
            event = buffer.subscribe(session_id)

            try:
                while not buffer.is_done(session_id):
                    event.clear()
                    try:
                        await asyncio.wait_for(event.wait(), timeout=30)
                    except asyncio.TimeoutError:
                        continue
                    new_messages = buffer.get_history(session_id, after_index=last_seen)
                    for i, h in enumerate(new_messages):
                        await websocket.send_text(json.dumps({
                            **h,
                            "index": last_seen + i,
                            "replay": False,
                        }))
                    last_seen += len(new_messages)
            finally:
                buffer.unsubscribe(session_id, event)

    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected for user %s", user_id)
    except Exception as e:
        logger.exception("WebSocket error")
        try:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": str(e),
            }))
        except Exception:
            pass


# ── Health check ──────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

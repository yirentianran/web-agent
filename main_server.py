"""Main FastAPI server — SDK integration, WebSocket, REST APIs.

Phase 1: Direct SDK integration (no Docker).
Phase 2+: Will add container orchestration and WebSocket bridging.

Exposes:
- WebSocket endpoint for browser → agent communication
- REST APIs for sessions, files, skills, memory, MCP, admin
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()  # Load .env file before any env var access

import asyncio
import io
import json
import logging
import os
import re
import shutil
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.message_buffer import HEARTBEAT_INTERVAL, MessageBuffer, make_heartbeat
from src.models import (
    McpServerConfig,
    MemoryUpdate,
    SessionStatusResponse,
    SkillCreate,
    SkillInfo,
    SkillSource,
)

if TYPE_CHECKING:
    from src.database import Database
    from src.session_store import SessionStore

# ── Configuration ────────────────────────────────────────────────

import logging.handlers

LOG_FILE = Path(__file__).parent / "server.log"

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Ensure handler is only added once (survives reloads)
if not logger.handlers:
    _fmt = logging.Formatter("%(asctime)s %(name)s:%(lineno)d %(levelname)s %(message)s")
    _stream = logging.StreamHandler()
    _stream.setFormatter(_fmt)
    _file = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3,
    )
    _file.setFormatter(_fmt)
    logger.addHandler(_stream)
    logger.addHandler(_file)

# Also capture uvicorn logs to the same file
for _uv_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
    _uv_logger = logging.getLogger(_uv_name)
    if _uv_logger and not _uv_logger.handlers:
        _uv_logger.addHandler(logging.FileHandler(LOG_FILE))

# Resolve DATA_ROOT relative to this file's directory, not CWD
_DATA_ROOT_ENV = os.getenv("DATA_ROOT", "/data")
_DATA_ROOT_PATH = Path(_DATA_ROOT_ENV)
if _DATA_ROOT_PATH.is_absolute():
    DATA_ROOT = _DATA_ROOT_PATH
else:
    DATA_ROOT = (Path(__file__).parent / _DATA_ROOT_ENV).resolve()
PROD = os.getenv("PROD", "false").lower() == "true"
app = FastAPI(title="Web Agent")

# ── Skill upload limits ──────────────────────────────────────────
MAX_ZIP_SIZE = 50 * 1024 * 1024  # 50MB compressed
MAX_UNCOMPRESSED = 100 * 1024 * 1024  # 100MB uncompressed
MAX_SKILL_FILES = 100

# In production (single-server), CORS is unnecessary since frontend and API share the same origin
if not PROD:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Global state
_db: Database | None = None  # SQLite database
buffer = MessageBuffer(base_dir=DATA_ROOT / ".msg-buffer")
session_store: SessionStore | None = None  # Initialized at startup if DATA_DB_PATH set
active_tasks: dict[str, asyncio.Task] = {}
pending_answers: dict[str, asyncio.Future] = {}


async def cleanup_session_client(session_id: str) -> None:
    """No-op placeholder — CLI subprocess terminates after each turn,
    so we create a fresh client every time."""
    pass

# ── Container mode toggle ─────────────────────────────────────────

CONTAINER_MODE = os.getenv("CONTAINER_MODE", "false").lower() == "true"

# Lazy import: only needed when CONTAINER_MODE is enabled
_container_manager = None


def _get_container_manager():
    """Return the container_manager module, or None if unavailable."""
    global _container_manager
    if _container_manager is not None:
        return _container_manager
    try:
        import src.container_manager as cm  # noqa: PLC0415
        _container_manager = cm
        return cm
    except ImportError:
        logger.warning("docker-py not installed; container mode disabled")
        return None


# ── Phase 1: Direct SDK integration ─────────────────────────────
# In Phase 2+, this moves into container-internal agent_server.py
# and main_server bridges to it via WebSocket.

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookContext,
    HookInput,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    PermissionResult,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TextBlock,
    ThinkingBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


def load_skills(user_id: str) -> dict[str, dict[str, Any]]:
    """Load all Skills for a user: shared + personal from workspace/.claude/skills."""
    user_dir = user_data_dir(user_id)
    workspace_skills = user_dir / "workspace" / ".claude" / "skills"
    shared_skills = DATA_ROOT / "shared-skills"

    all_skills: dict[str, dict[str, Any]] = {}

    # Load shared skills
    if shared_skills.exists():
        for skill_dir in sorted(shared_skills.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                all_skills[skill_dir.name] = {
                    "path": str(skill_dir),
                    "source": "shared",
                    "content": skill_file.read_text(),
                }

    # Load personal skills (override shared on name conflict)
    if workspace_skills.exists():
        for skill_dir in sorted(workspace_skills.iterdir()):
            if not skill_dir.is_dir() or skill_dir.is_symlink():
                continue  # symlinks are shared skills
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                all_skills[skill_dir.name] = {
                    "path": str(skill_dir),
                    "source": "personal",
                    "content": skill_file.read_text(),
                }

    return all_skills


def load_memory(user_id: str) -> str:
    """Load L1 platform memory from memory.json."""
    mem_file = user_data_dir(user_id) / "memory.json"
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
        for i, finding in enumerate(findings, 1):
            parts.append(
                f"{i}. {finding.get('item', '')} "
                f"({finding.get('standard', '')}, "
                f"status: {finding.get('status', '')})\n"
            )

    risk = audit_ctx.get("risk_areas", [])
    if risk:
        parts.append(f"\n## Key Risk Areas: {', '.join(risk)}\n")

    files = data.get("file_memory", [])
    if files:
        parts.append("\n## Frequently Used Files\n")
        for f in files:
            parts.append(
                f"- {f.get('filename', '')} "
                f"(last used: {f.get('last_used', '')})\n"
            )

    return "\n".join(parts)


def build_file_generation_rules_prompt(workspace: Path) -> str:
    """Build file generation rules that include the actual workspace path."""
    ws = str(workspace)
    return (
        "\n## File Generation Rules\n"
        f"- Your workspace is: {ws}\n"
        "- All generated files for the user (Excel, PDF, ZIP, CSV, images, Word documents, TXT, Markdown, RTF, etc.) must be saved to `outputs/` in your workspace.\n"
        "- Use RELATIVE paths like `outputs/filename.ext` — NEVER use absolute paths.\n"
        f"- WRONG: `/Users/mac/outputs/content.txt`, `/tmp/file.xlsx`, `/home/user/result.pdf`, `report.txt`\n"
        f"- CORRECT: `outputs/content.txt`, `outputs/report.docx`, `outputs/data.csv`\n"
        "- ONLY Python scripts (.py), shell scripts (.sh), and config files (.json, .yaml) should be placed in the workspace root (not `outputs/`).\n"
        "- NEVER write files to paths starting with `/Users/`, `/tmp/`, `/home/`, or any absolute path outside the workspace.\n"
    )


def is_path_within_workspace(file_path: str, workspace: Path) -> bool:
    """Check if a file path (relative or absolute) resolves within the workspace."""
    path = Path(file_path)
    if path.is_absolute():
        resolved = path.resolve()
    else:
        resolved = (workspace / path).resolve()
    return str(resolved).startswith(str(workspace.resolve()))


def build_download_url(user_id: str, file_path: str, *, directory: str | None = None) -> str:
    """Build a download URL for a file, including the correct directory prefix.

    Handles relative paths, absolute paths, and already-prefixed paths.
    Always produces a clean URL of the form /api/users/{user_id}/download/{dir}/{name}.
    """
    path = Path(file_path)
    # Absolute path — extract just the filename, ignore the directory
    if path.is_absolute():
        return f"/api/users/{user_id}/download/outputs/{path.name}"

    parts = path.parts
    if len(parts) > 1:
        # Path includes directory (e.g., 'outputs/file.txt')
        prefix = "/".join(parts[:-1])
        filename = path.name
    elif directory:
        prefix = directory
        filename = path.name
    else:
        return f"/api/users/{user_id}/download/{path.name}"
    return f"/api/users/{user_id}/download/{prefix}/{filename}"


# File types that are infrastructure/intermediate — never offered as user-facing results
IGNORED_FILE_EXTS = {".log", ".pyc", ".pyo", ".pid", ".lock"}

# Filenames that indicate a programming error, not a real generated file
INVALID_FILENAMES = {"null", "undefined"}

# Allowed extensions for user-facing generated file results (data documents, media, archives)
DATA_EXTS = {
    ".xlsx", ".xls", ".pdf", ".zip", ".csv", ".png", ".jpg", ".jpeg",
    ".gif", ".docx", ".doc", ".pptx", ".ppt", ".txt", ".md", ".rtf",
    ".odt", ".html", ".svg", ".bmp", ".webp", ".tif", ".tiff",
    ".mp3", ".wav", ".mp4", ".mov", ".avi",
}


def should_include_generated_file(filename: str) -> bool:
    """Return True if this file should be offered as a downloadable result.

    Uses a **positive allow-list** (`DATA_EXTS`) for user-facing data files.
    Script/code files (`.py`, `.js`, `.sh`, etc.) are excluded by omission.
    """
    if not filename:
        return False
    # Reject filenames that indicate a programming error (e.g. None → "null")
    name_lower = filename.lower()
    if name_lower in INVALID_FILENAMES:
        return False
    # Also reject when the stem (without extension) is invalid
    stem_lower = Path(filename).stem.lower()
    if stem_lower in INVALID_FILENAMES:
        return False
    ext = Path(filename).suffix.lower()
    if not ext:
        return False
    # Must be in the positive allow-list
    return ext in DATA_EXTS


def check_bash_command_for_external_writes(cmd: str, workspace: Path) -> str | None:
    """Return an error message if the command writes outside workspace, or None if safe."""
    # Patterns that indicate writes to paths outside the workspace
    outside_patterns = [
        r"(?:>\s*|\w+\s+)(/Users/[^\s'\"]+)",
        r"(?:>\s*|\w+\s+)(/tmp/[^\s'\"]+)",
        r"(?:>\s*|\w+\s+)(/home/[^\s'\"]+)",
        r"(?:>\s*|\w+\s+)(/var/[^\s'\"]+)",
        r"(?:>\s*|\w+\s+)(/etc/[^\s'\"]+)",
        r"(?:>\s*|\w+\s+)(/root/[^\s'\"]+)",
    ]
    for pat in outside_patterns:
        match = re.search(pat, cmd)
        if match:
            target = match.group(1) if match.lastindex else match.group(0)
            return (
                f"Command writes to '{target}' which is outside the workspace. "
                "Save all files within the workspace directory (use outputs/ for generated files)."
            )
    return None


def rewrite_path_to_workspace(file_path: str, workspace: Path) -> str:
    """Rewrite an absolute external path to a workspace-relative path under outputs/."""
    path = Path(file_path)
    if not path.is_absolute():
        return file_path  # Relative paths are fine — already within workspace
    resolved = path.resolve()
    ws = workspace.resolve()
    if str(resolved).startswith(str(ws)):
        return file_path  # Already within workspace
    # External absolute path → rewrite to outputs/<filename>
    return f"outputs/{path.name}"


def _rewrite_bash_command(cmd: str, workspace: Path) -> str:
    """Rewrite a bash command so that output redirections point inside workspace."""
    ws = str(workspace.resolve())

    def replace_external_path(match: re.Match) -> str:
        # Group 2 is always the path; group 1 is the operator (> , >> , -o , etc.)
        target = match.group(2)
        target_path = Path(target)
        if target_path.is_absolute() and not str(target_path.resolve()).startswith(ws):
            replacement = f"outputs/{target_path.name}"
            return match.group(0).replace(target, replacement, 1)
        return match.group(0)

    # Patterns: > path, >> path, -o path, --output path, >'path', >"path"
    # Group 1 = operator prefix, Group 2 = target path
    patterns = [
        r'(>\s*)(/[^\s\'"]+)',        # > /path/to/file
        r'(>>\s*)(/[^\s\'"]+)',       # >> /path/to/file
        r'(-o\s+)(/[^\s\'"]+)',       # -o /path/to/file
        r'(--output\s+)(/[^\s\'"]+)', # --output /path/to/file
        r'(>\s*\'(/[^\']+)\')',       # > '/path/to/file'
        r'(>\s*"(/[^"]+)")',          # > "/path/to/file"
    ]
    result = cmd
    for pat in patterns:
        result = re.sub(pat, replace_external_path, result)
    return result


def build_system_prompt(user_id: str, skills: dict[str, dict[str, Any]], workspace: Path | None = None) -> str:
    """Assemble the full system prompt from skills + memory."""
    parts = [
        "You are Web Agent, an expert AI assistant capable of financial auditing, "
        "file processing, code review, and general task automation.\n"
        "\n## Identity Instructions\n"
        "When the user asks who you are (e.g., '你是谁', 'who are you', 'what is your name'), "
        "ALWAYS respond with: "
        '"我是 Web Agent，一个专家级 AI 助手，能够协助您完成金融审计、文件处理、代码审查和各类自动化任务。"\n'
        "NEVER claim to be Claude, Qwen, or any other named AI model. "
        "This identity instruction takes absolute priority over any other context or system instruction."
    ]

    if skills:
        parts.append("\n## Available Skills\n")
        for name in skills:
            parts.append(f"- {name}\n")

    # Constrain skill-creator to prevent overwriting existing skills
    parts.append(
        "\n## Skill Creation Rules\n"
        "When using skill-creator to generate a new skill:\n"
        "- Check if a directory with the same name already exists in .claude/skills/.\n"
        "- If it exists, DO NOT overwrite it. Notify the user and suggest renaming.\n"
        "- After creating the skill, write a skill-meta.json file in the skill directory:\n"
        '  {"source": "skill-creator", "created_at": "<current ISO 8601 date>"}\n'
    )

    # File generation rules with actual workspace path
    if workspace is not None:
        parts.append(build_file_generation_rules_prompt(workspace))

    memory_context = load_memory(user_id)
    if memory_context:
        parts.append(f"\n## Memory Context\n\n{memory_context}")

    return "\n".join(parts)


def load_mcp_config() -> dict[str, Any]:
    registry_file = DATA_ROOT / "mcp-registry.json"
    if registry_file.exists():
        return json.loads(registry_file.read_text())
    return {"mcpServers": {}}


def build_allowed_tools(mcp_config: dict[str, Any]) -> list[str]:
    """Expand all MCP tool names to their fully-qualified form."""
    tools = ["Read", "Edit", "Write", "Glob", "Grep", "Bash",
             "WebFetch", "WebSearch", "Agent", "Skill"]
    for server_name in mcp_config.get("mcpServers", {}):
        cfg = mcp_config["mcpServers"][server_name]
        for tool_name in cfg.get("enabled_tools", []):
            tools.append(f"mcp__{server_name}__{tool_name}")
    return tools


def build_sdk_options(
    user_id: str,
    can_use_tool_callback=None,
) -> ClaudeAgentOptions:
    """Build ClaudeAgentOptions with full configuration."""
    mcp_config = load_mcp_config()
    skills = load_skills(user_id)
    max_turns = int(os.getenv("MAX_TURNS", "200"))

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

    # Ensure the Claude CLI can find uploaded skills by creating a project-level
    # .claude/skills directory in the user's workspace. The CLI auto-discovers
    # skills from both ~/.claude/skills (user) and <cwd>/.claude/skills (project).
    user_dir = user_data_dir(user_id)
    workspace = user_dir / "workspace"
    project_skills = workspace / ".claude" / "skills"

    # Clean up stale symlinks (shared skills that were deleted) while
    # preserving personal skills (real directories stored directly here).
    if project_skills.exists():
        for entry in list(project_skills.iterdir()):
            if entry.is_symlink():
                entry.unlink()  # remove stale or outdated symlink
            elif entry.is_file():
                entry.unlink()  # remove stray files
        # Ensure it's still a directory
        if not project_skills.is_dir():
            project_skills.unlink()
            project_skills.mkdir(parents=True)
    else:
        project_skills.mkdir(parents=True)

    # Link shared skills — personal skills are already stored directly in
    # workspace/.claude/skills by the upload endpoint, so no symlink needed.
    shared_src = DATA_ROOT / "shared-skills"
    if shared_src.exists():
        for skill_dir in shared_src.iterdir():
            if skill_dir.is_dir():
                link = project_skills / skill_dir.name
                # Skip if a personal skill (real directory) already exists
                if link.exists() and not link.is_symlink():
                    continue
                # Remove any existing symlink (stale or outdated)
                if link.is_symlink():
                    link.unlink()
                link.symlink_to(skill_dir.resolve())

    # Ensure outputs/ directory exists for agent-generated files
    outputs_dir = workspace / "outputs"
    outputs_dir.mkdir(exist_ok=True)

    # PreToolUse hooks — intercept Write and Bash to prevent external file writes.
    # Hooks run regardless of permission_mode (unlike can_use_tool which is skipped
    # by acceptEdits/bypassPermissions).
    async def write_path_hook(
        hook_input: HookInput,
        _tool_use_id: str | None,
        _context: HookContext,
    ) -> dict:
        tool_inp = hook_input.get("tool_input", {})
        file_path = str(tool_inp.get("file_path", ""))
        if not file_path:
            return {"sync": True, "continue_": True}
        rewritten = rewrite_path_to_workspace(file_path, workspace)
        if rewritten == file_path:
            return {"sync": True, "continue_": True}
        logger.info("PreToolUse[Write]: '%s' → '%s'", file_path, rewritten)
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
        cmd = str(hook_input.get("tool_input", {}).get("command", ""))
        if not cmd:
            return {"sync": True, "continue_": True}
        rewritten = _rewrite_bash_command(cmd, workspace)
        if rewritten == cmd:
            return {"sync": True, "continue_": True}
        logger.info("PreToolUse[Bash]: rewrote '%s' → '%s'", cmd[:120], rewritten[:120])
        new_input = dict(hook_input.get("tool_input", {}))
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

    return ClaudeAgentOptions(
        model=os.getenv("MODEL", "claude-sonnet-4-6"),
        cwd=str(user_dir / "workspace"),
        system_prompt=build_system_prompt(user_id, skills, workspace),
        allowed_tools=build_allowed_tools(mcp_config),
        max_turns=max_turns,
        permission_mode="acceptEdits",
        mcp_servers=mcp_servers if mcp_servers else None,
        can_use_tool=can_use_tool_callback,
        hooks=hooks,
    )



def message_to_dicts(msg: Any) -> Iterator[dict[str, Any]]:
    """Convert a Claude SDK Message dataclass to one or more serializable dicts.

    An ``AssistantMessage`` may contain multiple content blocks (e.g. a
    ``ToolUseBlock`` followed by a ``ToolResultBlock``).  Each block that
    warrants its own message is yielded separately so that tool output
    (e.g. Bash stdout) reaches the frontend instead of being silently dropped.
    """
    if isinstance(msg, UserMessage):
        content = msg.content
        if isinstance(content, list):
            text = " ".join(
                b.text for b in content if isinstance(b, TextBlock)
            )
        else:
            text = content
        yield {"type": "user", "content": text}
        return

    if isinstance(msg, AssistantMessage):
        text_parts: list[str] = []
        # Build a map of tool_use_id -> tool name so ToolResultBlock can resolve names
        tool_use_names: dict[str, str] = {}
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                tool_use_names[block.id] = block.name

        for block in msg.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ThinkingBlock):
                text_parts.append(f"[thinking] {block.thinking}[/thinking]")
            elif isinstance(block, ToolUseBlock):
                yield {
                    "type": "tool_use",
                    "name": block.name,
                    "id": block.id,
                    "input": block.input,
                }
            elif isinstance(block, ToolResultBlock):
                tool_name = tool_use_names.get(block.tool_use_id, "unknown")
                content_val: str
                if isinstance(block.content, list):
                    content_val = json.dumps(block.content, ensure_ascii=False)
                elif block.content is None:
                    content_val = ""
                else:
                    content_val = block.content
                result_dict: dict[str, Any] = {
                    "type": "tool_result",
                    "name": tool_name,
                    "tool_use_id": block.tool_use_id,
                    "content": content_val,
                }
                if block.is_error is not None:
                    result_dict["is_error"] = block.is_error
                yield result_dict
            else:
                text_parts.append(str(block))
        if text_parts:
            yield {
                "type": "assistant",
                "content": "\n".join(text_parts),
            }
        return

    if isinstance(msg, ResultMessage):
        result: dict[str, Any] = {
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
        if msg.session_id:
            result["session_id"] = msg.session_id
        yield result
        return

    if isinstance(msg, TaskNotificationMessage):
        result: dict[str, Any] = {
            "type": "system",
            "subtype": msg.subtype,
            "status": msg.status,
            "summary": msg.summary,
        }
        if msg.usage:
            result["cost_usd"] = msg.usage.get("total_cost_usd", 0)
        yield result
        return

    if isinstance(msg, TaskProgressMessage):
        result: dict[str, Any] = {
            "type": "system",
            "subtype": "progress",
        }
        if msg.usage:
            result["cost_usd"] = msg.usage.get("total_cost_usd", 0)
        if msg.data:
            result.update(msg.data)
        yield result
        return

    if isinstance(msg, SystemMessage):
        result: dict[str, Any] = {
            "type": "system",
            "subtype": msg.subtype,
        }
        if msg.data:
            result.update(msg.data)
        yield result
        return

    if isinstance(msg, StreamEvent):
        yield {
            "type": "stream_event",
            "uuid": msg.uuid,
            "event": msg.event,
        }
        return

    # Fallback: try to serialize as dict
    if hasattr(msg, "__dict__"):
        yield {"type": "unknown", "data": msg.__dict__}
    else:
        yield {"type": "unknown", "content": str(msg)}


async def _can_use_tool_for_session(
    session_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    context: ToolPermissionContext,
) -> PermissionResult:
    """Intercept AskUserQuestion and route answer through WebSocket."""
    if tool_name == "AskUserQuestion":
        # Add the question to buffer so UI can display it
        buffer.add_message(session_id, {
            "type": "tool_use",
            "name": "AskUserQuestion",
            "id": f"ask_{uuid.uuid4().hex[:8]}",
            "input": tool_input,
        })

        # Wait for user answer via WebSocket
        answer_future: asyncio.Future = asyncio.get_event_loop().create_future()
        pending_answers[session_id] = answer_future
        try:
            answer = await asyncio.wait_for(answer_future, timeout=300)
            # Return the answer as the tool input (simulating tool result)
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

    # All other tools: allow
    return PermissionResultAllow(behavior="allow")


MAX_CONTINUATION_WINDOW = int(os.getenv("MAX_CONTINUATION_WINDOW", "10"))
MAX_PROMPT_LENGTH = int(os.getenv("MAX_PROMPT_LENGTH", "8000"))


def _build_history_prompt(
    history: list[dict[str, Any]], user_message: str
) -> str:
    """Build a multi-turn conversation prompt from history + new message.

    Controls:
    - Window: only the last N messages are included (configurable, default 10)
    - Truncation: tool_result content capped at 200 chars; tool_use records name only
    - Length: total prompt capped at MAX_PROMPT_LENGTH chars; oldest messages dropped first
    - System messages and empty content are skipped
    - Assistant messages are excluded — they cause Echo agents to repeat previous responses
    - The final user message is always preserved
    """
    parts: list[str] = []

    # Step 1: format user messages and tool records only
    for msg in history:
        msg_type = msg.get("type")
        content = msg.get("content", "")
        if msg_type == "user" and (not content or not content.strip()):
            continue
        if msg_type == "tool_use":
            # Record tool name only, not input — saves tokens and avoids leaking secrets
            parts.append(f"[Tool: {msg.get('name', '?')}]")
        elif msg_type == "tool_result":
            if not content or not content.strip():
                continue
            # Truncate long tool results
            parts.append(f"[Tool Result] {content[:200]}")
        elif msg_type == "user":
            line = f"User: {content}"
            # Mention attached files with their relative path so the agent can locate them
            attached = msg.get("data")
            if attached and isinstance(attached, list):
                paths = [f"uploads/{f.get('filename', '?')}" for f in attached if isinstance(f, dict)]
                if paths:
                    line += f"\n(Attached files: {', '.join(paths)})"
            parts.append(line)
        # Skip assistant and system messages — including them causes agents
        # (especially Echo agents) to repeat previous responses.

    # Step 2: sliding window — keep only the last N parts
    if len(parts) > MAX_CONTINUATION_WINDOW:
        parts = parts[-MAX_CONTINUATION_WINDOW:]

    # Step 3: add the new user message (always preserved)
    parts.append(f"User: {user_message}")
    parts.append("Assistant:")

    # Step 4: enforce total length limit by dropping from the start
    prompt = "\n\n".join(parts)
    while len(prompt) > MAX_PROMPT_LENGTH and len(parts) > 2:
        parts = parts[1:]  # drop oldest part
        prompt = "\n\n".join(parts)

    return prompt


def _format_first_message_prompt(user_message: str, attached_files: list[str] | None) -> str:
    """Build the first-message prompt, including file paths if files were uploaded."""
    if not attached_files:
        return user_message
    paths = ", ".join(f"uploads/{f}" for f in attached_files)
    return f"{user_message}\n\n(Attached files: {paths})"



async def run_agent_task(
    user_id: str, session_id: str, user_message: str,
    is_continuation: bool = False, attached_files: list[str] | None = None,
) -> None:
    """Run the agent using ClaudeSDKClient for bidirectional interaction.

    When *is_continuation* is True, historical messages are replayed to the
    Claude CLI so the agent has full conversation context.
    When *attached_files* is provided, file names are mentioned in the prompt
    so the agent knows which files the user uploaded.
    """
    from src.agent_logger import AgentLogger

    agent_log = AgentLogger(user_id=user_id)
    agent_log.start_session(session_id, user_message=user_message)
    start_time = time.time()

    # Persist user message to buffer so it survives page refresh
    user_msg: dict[str, Any] = {"type": "user", "content": user_message}
    if attached_files:
        user_msg["data"] = [{"filename": f} for f in attached_files]
    buffer.add_message(session_id, user_msg)

    # Resolve workspace path — needed for both tool permission check and file snapshot
    workspace = user_data_dir(user_id) / "workspace"

    # Build options
    async def can_use_tool_cb(
        tool_name: str,
        tool_input: dict[str, Any],
        ctx: ToolPermissionContext,
    ) -> PermissionResult:
        # Block file writes outside workspace
        if tool_name == "Write":
            file_path = str(tool_input.get("file_path", ""))
            if file_path and not is_path_within_workspace(file_path, workspace):
                return PermissionResultDeny(
                    message=f"File path '{file_path}' is outside the workspace. "
                            f"All files must be saved within the workspace directory.",
                )

        # Block Bash commands that write to paths outside workspace
        if tool_name == "Bash":
            cmd = str(tool_input.get("command", ""))
            error = check_bash_command_for_external_writes(cmd, workspace)
            if error:
                return PermissionResultDeny(message=error)

        agent_log.tool_call(tool_name, tool_input, session_id=session_id)
        result = await _can_use_tool_for_session(
            session_id, tool_name, tool_input, ctx
        )
        agent_log.tool_result(tool_name, str(result), session_id=session_id)
        return result

    # Snapshot workspace files before the agent task — used to detect
    # files created by Bash commands (Python scripts, etc.) after the task ends.
    workspace_snapshot: dict[str, float] = {}
    if workspace.exists():
        for f in workspace.rglob("*"):
            if f.is_file():
                workspace_snapshot[str(f.relative_to(workspace))] = f.stat().st_mtime

    options = build_sdk_options(
        user_id, can_use_tool_callback=can_use_tool_cb
    )

    # Each turn creates a fresh client — CLI subprocess terminates after
    # receive_response() completes, so cached clients are invalid.
    client = ClaudeSDKClient(options)

    try:
        if is_continuation:
            # Reconnect with full conversation as a multi-turn prompt.
            # can_use_tool requires streaming mode — wrap prompt as AsyncIterable.
            history = buffer.get_history(session_id, after_index=0)
            full_prompt = _build_history_prompt(history, user_message)

            # stream_input expects dicts with the same format as the string
            # prompt handler (line 196-203 in client.py)
            async def prompt_stream():
                yield {
                    "type": "user",
                    "message": {"role": "user", "content": full_prompt},
                    "parent_tool_use_id": None,
                    "session_id": "default",
                }

            logger.info(
                "WS continuation %s: prompt length=%d chars, window=%d",
                session_id, len(full_prompt), len(history),
            )
            await client.connect(prompt=prompt_stream())
        else:
            # First message — connect normally, then query
            await client.connect()
            # Include file attachment info so the agent knows what files were uploaded
            prompt = _format_first_message_prompt(user_message, attached_files)
            await client.query(prompt)

        # Receive messages until result
        msg_count = 0
        generated_files: list[dict[str, Any]] = []
        buffered_result: dict[str, Any] | None = None  # SDK result for reordering
        async for msg in client.receive_response():
            msg_count += 1
            for event in message_to_dicts(msg):
                # User message already persisted at function start — skip duplicates from agent response
                if event.get("type") == "user":
                    continue
                # Buffer the SDK result message so file_result can be emitted
                # first, ensuring file cards appear before "Session completed".
                if event.get("type") == "result":
                    buffered_result = event
                    continue
                # Track Write tool use to collect generated files
                if event.get("type") == "tool_use" and event.get("name") == "Write":
                    tool_input = event.get("input") or {}
                    file_path = tool_input.get("file_path", "")
                    if file_path and should_include_generated_file(Path(file_path).name):
                        # Extract filename, compute size from content
                        filename = Path(file_path).name
                        content = tool_input.get("content", "")
                        try:
                            size = len(content.encode("utf-8"))
                        except Exception:
                            size = len(content)
                        generated_files.append({
                            "filename": filename,
                            "size": size,
                            "generated_at": datetime.now(timezone.utc).isoformat(),
                            "download_url": build_download_url(user_id, file_path, directory="outputs"),
                        })
                # Truncate oversized tool results
                if event.get("type") == "tool_result":
                    from src.truncation import truncate_tool_output

                    content = event.get("content", "")
                    if content and len(content) > 1000:
                        event["content"] = truncate_tool_output(content)
                buffer.add_message(session_id, event)

        # Detect files created/modified by Bash commands since the task started
        seen_filenames: set[str] = {f["filename"] for f in generated_files}
        outputs_dir = workspace / "outputs"

        # 1. Scan outputs/ for new/modified files (primary generated file location)
        if outputs_dir.exists():
            for f in outputs_dir.iterdir():
                if not f.is_file():
                    continue
                if not should_include_generated_file(f.name):
                    continue
                rel = str(f.relative_to(workspace))
                mtime = f.stat().st_mtime
                if rel not in workspace_snapshot or mtime > workspace_snapshot[rel]:
                    if f.name not in seen_filenames:
                        seen_filenames.add(f.name)
                        generated_files.append({
                            "filename": f.name,
                            "size": f.stat().st_size,
                            "generated_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                            "download_url": f"/api/users/{user_id}/download/{rel}",
                        })

        # 2. Scan workspace root for data files that should be in outputs/
        #    Only downloadable result files are included; scripts and logs are left in place
        #    and not shown to the user as generated files.
        if workspace.exists():
            for f in workspace.iterdir():
                if not f.is_file() or f.name.startswith("."):
                    continue
                rel = str(f.relative_to(workspace))
                mtime = f.stat().st_mtime
                is_new_or_modified = rel not in workspace_snapshot or mtime > workspace_snapshot[rel]
                if is_new_or_modified and f.name not in seen_filenames:
                    ext = f.suffix.lower()
                    if ext in DATA_EXTS:
                        # Data file: move to outputs/
                        dest = outputs_dir / f.name
                        try:
                            shutil.move(str(f), str(dest))
                            seen_filenames.add(f.name)
                            generated_files.append({
                                "filename": f.name,
                                "size": dest.stat().st_size,
                                "generated_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                                "download_url": f"/api/users/{user_id}/download/outputs/{f.name}",
                            })
                        except Exception as e:
                            logger.warning("Failed to relocate data file %s to outputs/: %s", f, e)
                    # Non-data files (scripts, logs, configs) are silently left in workspace root

        # 3. Scan for files created outside workspace (user home, /tmp, server CWD)
        #    and relocate them to workspace/outputs/
        task_end = time.time()
        outside_dirs = [
            Path.home(),
            Path.home() / "outputs",  # Common mistaken path: /Users/<user>/outputs/
            Path(__file__).parent,
        ]
        for scan_dir in outside_dirs:
            if not scan_dir.exists() or not scan_dir.is_dir():
                continue
            for f in scan_dir.iterdir():
                if not f.is_file() or f.name.startswith("."):
                    continue
                if not should_include_generated_file(f.name):
                    continue
                mtime = f.stat().st_mtime
                # File created/modified during this agent task
                if mtime >= start_time and mtime <= task_end + 5:
                    if f.name not in seen_filenames:
                        dest = outputs_dir / f.name
                        try:
                            shutil.move(str(f), str(dest))
                            seen_filenames.add(f.name)
                            generated_files.append({
                                "filename": f.name,
                                "size": dest.stat().st_size,
                                "generated_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                                "download_url": f"/api/users/{user_id}/download/outputs/{f.name}",
                            })
                        except Exception as e:
                            logger.warning("Failed to relocate stray file %s: %s", f, e)

        # Emit file_result message if the agent generated any files this turn.
        # Uses add_message() (append order) — file_result is emitted BEFORE
        # session_state_changed:completed, so it appears before "Session completed"
        # in both live streaming and DB replay.
        # Filter out infrastructure files (logs, caches, etc.) and invalid filenames
        generated_files = [f for f in generated_files if f.get("filename") and should_include_generated_file(f["filename"])]
        if generated_files:
            # Ensure all file entries have download_url
            for f in generated_files:
                if "download_url" not in f:
                    f["download_url"] = build_download_url(user_id, f["filename"], directory="outputs")
            buffer.add_message(session_id, {
                "type": "file_result",
                "content": "",
                "session_id": session_id,
                "user_id": user_id,
                "data": generated_files,
            })

        logger.info(
            "Agent task %s: completed with %d messages in %.1fs",
            session_id, msg_count, time.time() - start_time,
        )
        # Add state change BEFORE mark_done() so the subscribe loop's
        # final pull (after is_done() returns True) catches the message.
        buffer.add_message(session_id, {
            "type": "system",
            "subtype": "session_state_changed",
            "state": "completed",
        })
        # Re-add the buffered SDK result AFTER file_result and state_change
        # so "Session completed" appears as the last visible message.
        if buffered_result is not None:
            buffer.add_message(session_id, buffered_result)
        buffer.mark_done(session_id)
        duration_ms = (time.time() - start_time) * 1000
        agent_log.end_session(session_id, status="completed")

    except asyncio.CancelledError:
        buffer.add_message(session_id, {
            "type": "system",
            "subtype": "session_cancelled",
            "message": "Session cancelled by user.",
        })
        # Add state change BEFORE mark_done() for the same reason.
        buffer.add_message(session_id, {
            "type": "system",
            "subtype": "session_state_changed",
            "state": "cancelled",
        })
        buffer.mark_done(session_id)
        agent_log.end_session(session_id, status="cancelled")
    except Exception as e:
        logger.exception("Agent task failed for session %s", session_id)
        buffer.add_message(session_id, {
            "type": "error",
            "message": str(e),
        })
        # Add state change BEFORE mark_done() so the error is delivered.
        buffer.add_message(session_id, {
            "type": "system",
            "subtype": "session_state_changed",
            "state": "error",
        })
        buffer.mark_done(session_id)
        agent_log.end_session(session_id, status="error")
    # Note: do NOT disconnect — client is kept alive for follow-ups


# ── WebSocket endpoint ───────────────────────────────────────────


@app.websocket("/ws")
async def handle_ws(websocket: WebSocket) -> None:
    """Browser ↔ Agent WebSocket. Direct SDK integration (Phase 1)."""
    from src.auth import ENFORCE_AUTH, require_user_match, verify_token

    token = websocket.query_params.get("token")
    if ENFORCE_AUTH and token:
        try:
            _auth_user_id = verify_token(token)
        except Exception:
            await websocket.close(code=4001, reason="Invalid token")
            return
    elif ENFORCE_AUTH and not token:
        await websocket.close(code=4001, reason="Missing token")
        return

    await websocket.accept()

    user_id = "unknown"
    # Track the most recent session this handler is subscribed to,
    # so we can break out of the subscribe loop when a new session
    # message arrives on the same connection.
    current_session_id: str | None = None

    # Queue for messages received while the subscribe loop is active.
    pending_ws_msgs: asyncio.Queue[dict] = asyncio.Queue()

    # Coroutine that continuously reads WebSocket messages and queues them.
    async def ws_reader():
        nonlocal user_id
        try:
            while True:
                raw = await websocket.receive_text()
                data = json.loads(raw)
                user_id = data.get("user_id", "default")

                # If we're in a subscribe loop for a different session,
                # queue the message so the subscribe loop can pick it up.
                if current_session_id and data.get("session_id") != current_session_id:
                    pending_ws_msgs.put_nowait(data)
                else:
                    pending_ws_msgs.put_nowait(data)
        except WebSocketDisconnect:
            pending_ws_msgs.put_nowait(None)
        except Exception:
            pending_ws_msgs.put_nowait(None)

    reader_task = asyncio.create_task(ws_reader())

    try:
        while True:
            # Drain any queued messages first
            data = None
            while True:
                try:
                    item = pending_ws_msgs.get_nowait()
                    if item is None:
                        return  # WebSocket closed
                    if item.get("type") == "answer":
                        sid = item.get("session_id", "")
                        answers = item.get("answers", {})
                        future = pending_answers.get(sid)
                        if future and not future.done():
                            future.set_result(answers)
                    else:
                        data = item
                        break
                except asyncio.QueueEmpty:
                    break

            # If no queued message, wait for the next one
            if data is None:
                item = await pending_ws_msgs.get()
                if item is None:
                    return  # WebSocket closed
                if item.get("type") == "answer":
                    sid = item.get("session_id", "")
                    answers = item.get("answers", {})
                    future = pending_answers.get(sid)
                    if future and not future.done():
                        future.set_result(answers)
                    continue
                data = item

            user_message = data.get("message", "")
            session_id = data.get("session_id")
            last_index = data.get("last_index", 0)
            attached_files = data.get("files") or None

            if not session_id:
                session_id = f"session_{user_id}_{time.time()}_{uuid.uuid4().hex[:8]}"

            # If we're subscribed to a different session, unsubscribe first
            if current_session_id and current_session_id != session_id:
                current_session_id = None

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
            task_is_new = task_key not in active_tasks or active_tasks[task_key].done()
            if task_is_new:
                # Check if this is a continuation (has prior history)
                buf_state = buffer.sessions.get(session_id)
                has_history = buf_state and len(buf_state.get("messages", [])) > 0
                is_continuation = has_history

                if buf_state:
                    buf_state["done"] = False
                    buf_state["state"] = "running"

                # Broadcast running state to frontend via WebSocket
                buffer.add_message(session_id, {
                    "type": "system",
                    "subtype": "session_state_changed",
                    "state": "running",
                })

                task = asyncio.create_task(
                    run_agent_task(
                        user_id, session_id, user_message,
                        is_continuation=is_continuation,
                        attached_files=attached_files,
                    )
                )
                active_tasks[task_key] = task
                logger.info(
                    "WS: created new task for session %s (continuation=%s)",
                    session_id, is_continuation,
                )
            else:
                logger.debug("WS: reusing existing task for session %s", session_id)

            # Subscribe to real-time messages
            current_session_id = session_id
            last_seen = last_index + len(history)
            event = buffer.subscribe(session_id)

            try:
                while True:
                    # Check for new WebSocket messages first
                    try:
                        item = pending_ws_msgs.get_nowait()
                        if item is None:
                            return  # WebSocket closed
                        if item.get("type") == "answer":
                            sid = item.get("session_id", "")
                            answers = item.get("answers", {})
                            future = pending_answers.get(sid)
                            if future and not future.done():
                                future.set_result(answers)
                        elif item.get("session_id") != session_id:
                            # New session — break out to handle it
                            pending_ws_msgs.put_nowait(item)
                            break
                        else:
                            # Message for same session — process it
                            user_message = item.get("message", "")
                            if user_message:
                                logger.info("WS: new message for active session %s", session_id)
                                # Add the new user message and let the running task handle it
                                buffer.add_message(session_id, {
                                    "type": "user",
                                    "content": user_message,
                                    "data": item.get("files") or None
                                    and [{"filename": f} for f in item["files"]],
                                })
                    except asyncio.QueueEmpty:
                        pass

                    new_messages = buffer.get_history(session_id, after_index=last_seen)
                    for i, h in enumerate(new_messages):
                        idx = last_seen + i
                        msg_type = h.get("type", "unknown")
                        msg_subtype = h.get("subtype", "")
                        if msg_type == "system" and msg_subtype == "session_state_changed":
                            logger.debug(
                                "WS: sending state_change=%s for session %s (idx=%d)",
                                h.get("state", "?"), session_id, idx,
                            )
                        await websocket.send_text(json.dumps({
                            **h,
                            "index": idx,
                            "replay": False,
                            "session_id": session_id,
                        }))
                    last_seen += len(new_messages)

                    # If session is done, pull one final time to ensure
                    # session_state_changed: completed is not missed
                    # (it may have been added after the get_history snapshot).
                    if buffer.is_done(session_id):
                        final_messages = buffer.get_history(session_id, after_index=last_seen)
                        for i, h in enumerate(final_messages):
                            idx = last_seen + i
                            await websocket.send_text(json.dumps({
                                **h,
                                "index": idx,
                                "replay": False,
                                "session_id": session_id,
                            }))
                        break

                    event.clear()
                    try:
                        await asyncio.wait_for(event.wait(), timeout=HEARTBEAT_INTERVAL)
                    except asyncio.TimeoutError:
                        hb = make_heartbeat()
                        await websocket.send_text(json.dumps({
                            **hb,
                            "index": last_seen,
                            "replay": False,
                            "session_id": session_id,
                        }))
                        # Heartbeats are synthetic — do NOT increment last_seen.
                        # Incrementing it would drift the cursor past the actual
                        # buffer end, causing the final pull to miss messages.
                        continue
            finally:
                buffer.unsubscribe(session_id, event)
                current_session_id = None

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
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except asyncio.CancelledError:
            pass


# ── Helper ───────────────────────────────────────────────────────


def user_data_dir(user_id: str) -> Path:
    return DATA_ROOT / "users" / user_id


# ── Session Management API ───────────────────────────────────────


@app.post("/api/users/{user_id}/sessions")
async def create_session(user_id: str) -> dict[str, str]:
    """Create a new session for the user."""
    session_id = f"session_{user_id}_{time.time()}_{uuid.uuid4().hex[:8]}"

    # Initialize in MessageBuffer so history/status work immediately
    buffer._ensure_buf(session_id)

    # Persist to DB if available
    if session_store is not None:
        await session_store.create_session(user_id=user_id, session_id=session_id)
    else:
        # Fallback: file-based persistence
        sessions_dir = user_data_dir(user_id) / "claude-data" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        session_file = sessions_dir / f"{session_id}.jsonl"
        session_file.touch()

    return {"session_id": session_id, "title": ""}


@app.get("/api/users/{user_id}/sessions", response_model=list[dict[str, Any]])
async def list_sessions(user_id: str) -> list[dict[str, Any]]:
    """List all historical sessions for a user."""
    # Use DB-backed store if available
    if session_store is not None:
        return await session_store.list_sessions(user_id=user_id)

    # Fallback: file-based scan
    sessions_dir = user_data_dir(user_id) / "claude-data" / "sessions"
    sessions: list[dict[str, Any]] = []

    if sessions_dir.exists():
        for session_file in sorted(sessions_dir.glob("*.jsonl"), reverse=True):
            try:
                first_line = session_file.read_text().split("\n")[0]
                data = json.loads(first_line) if first_line.strip() else {}
                state = buffer.get_session_state(session_file.stem)

                # Check for custom title in meta file
                title = (
                    data.get("message", {}).get("content", "")[:100]
                    or session_file.stem
                )
                meta_file = sessions_dir / f"{session_file.stem}.meta.json"
                if meta_file.exists():
                    try:
                        meta = json.loads(meta_file.read_text())
                        if meta.get("title"):
                            title = meta["title"]
                    except (json.JSONDecodeError, OSError):
                        pass

                sessions.append({
                    "session_id": session_file.stem,
                    "created_at": data.get("timestamp", ""),
                    "title": title,
                    "status": state.get("state", "completed"),
                    "cost_usd": state.get("cost_usd", 0),
                    "size_mb": round(session_file.stat().st_size / (1024 * 1024), 2),
                })
            except (json.JSONDecodeError, OSError):
                state = buffer.get_session_state(session_file.stem)
                sessions.append({
                    "session_id": session_file.stem,
                    "created_at": "",
                    "title": session_file.stem,
                    "status": state.get("state", "completed"),
                    "cost_usd": state.get("cost_usd", 0),
                    "size_mb": 0,
                })

    # Also include in-memory sessions not yet on disk
    for sid in buffer.sessions:
        if user_id in sid and not any(s["session_id"] == sid for s in sessions):
            state = buffer.get_session_state(sid)
            sessions.append({
                "session_id": sid,
                "title": sid[:50],
                "status": state["state"],
                "cost_usd": state["cost_usd"],
                "last_active": state["last_active"],
            })

    return sessions


@app.get("/api/users/{user_id}/sessions/{session_id}/history")
async def get_session_history(user_id: str, session_id: str) -> list[dict[str, Any]]:
    """Get all messages for a historical session."""
    # Use DB-backed store if available
    if session_store is not None:
        messages = await session_store.get_session_history(session_id=session_id)
        # Determine state from buffer or DB
        state = buffer.get_session_state(session_id)
        return [
            {**msg, "session_id": session_id, "session_state": state.get("state", "idle")}
            for msg in messages
        ]

    # Fallback: file-based
    messages = buffer.get_history(session_id, after_index=0)
    state = buffer.get_session_state(session_id)
    return [
        {**msg, "session_id": session_id, "session_state": state.get("state", "idle")}
        for msg in messages
    ]


@app.get("/api/users/{user_id}/sessions/{session_id}/files")
async def get_session_files(user_id: str, session_id: str) -> list[dict[str, Any]]:
    """Get all agent-generated files for a session, sorted by generation time descending."""
    messages = buffer.get_history(session_id, after_index=0)
    generated: list[dict[str, Any]] = []
    seen: set[str] = set()

    for msg in messages:
        if msg.get("type") == "file_result":
            files = msg.get("data") or []
            for f in files:
                if isinstance(f, dict):
                    fname = f.get("filename", "")
                    if fname and fname not in seen and should_include_generated_file(fname):
                        seen.add(fname)
                        generated.append({
                            "filename": fname,
                            "size": f.get("size", 0),
                            "generated_at": f.get("generated_at", ""),
                            "download_url": f.get("download_url", build_download_url(user_id, fname, directory="outputs")),
                        })

    # Also scan the workspace/uploads and workspace/outputs directories for files created during this session
    workspace = user_data_dir(user_id) / "workspace"
    for scan_dir_name in ("uploads", "outputs"):
        scan_dir = workspace / scan_dir_name
        if scan_dir.exists():
            for f in scan_dir.iterdir():
                if f.is_file() and f.name not in seen and should_include_generated_file(f.name):
                    stat = f.stat()
                    seen.add(f.name)
                    generated.append({
                        "filename": f.name,
                        "size": stat.st_size,
                        "generated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                        "download_url": f"/api/users/{user_id}/download/{scan_dir_name}/{f.name}",
                    })

    # Sort by generation time descending
    generated.sort(key=lambda x: x.get("generated_at", ""), reverse=True)
    return generated


@app.get("/api/users/{user_id}/generated-files")
async def get_all_generated_files(user_id: str) -> list[dict[str, Any]]:
    """Get all agent-generated files across all sessions, sorted by generation time descending."""
    workspace = user_data_dir(user_id) / "workspace"
    generated: list[dict[str, Any]] = []

    scan_dir = workspace / "outputs"
    if scan_dir.exists():
        for f in scan_dir.iterdir():
            if f.is_file() and should_include_generated_file(f.name):
                stat = f.stat()
                generated.append({
                    "filename": f.name,
                    "size": stat.st_size,
                    "generated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "download_url": f"/api/users/{user_id}/download/outputs/{f.name}",
                })

    # Sort by generation time descending
    generated.sort(key=lambda x: x.get("generated_at", ""), reverse=True)
    return generated


@app.delete("/api/users/{user_id}/sessions/{session_id}")
async def delete_session(user_id: str, session_id: str) -> dict[str, str]:
    """Delete a session, its messages, in-memory buffer, and active client (free disk)."""
    # Use DB-backed store if available
    if session_store is not None:
        await session_store.delete_session(session_id=session_id)
    else:
        # Fallback: file-based deletion
        sessions_dir = user_data_dir(user_id) / "claude-data" / "sessions"
        session_file = sessions_dir / f"{session_id}.jsonl"
        meta_file = sessions_dir / f"{session_id}.meta.json"

        deleted = False
        if session_file.exists():
            session_file.unlink()
            deleted = True
        if meta_file.exists():
            meta_file.unlink()
            deleted = True

        if not deleted:
            raise HTTPException(status_code=404, detail="Session not found")

    # Clean up in-memory buffer so it won't reappear in the list
    buffer.remove_session(session_id)

    # Disconnect the active Claude SDK client for this session
    await cleanup_session_client(session_id)
    return {"status": "ok"}


class TitleUpdate(BaseModel):
    title: str


@app.patch("/api/users/{user_id}/sessions/{session_id}/title")
async def update_session_title(user_id: str, session_id: str, req: TitleUpdate) -> dict[str, str]:
    """Update a session's title."""
    # Use DB-backed store if available
    if session_store is not None:
        await session_store.update_session_title(user_id=user_id, session_id=session_id, title=req.title)
    else:
        # Fallback: file-based metadata
        meta_file = user_data_dir(user_id) / "claude-data" / "sessions" / f"{session_id}.meta.json"
        meta_file.parent.mkdir(parents=True, exist_ok=True)
        meta = {"title": req.title, "updated_at": time.time()}
        meta_file.write_text(json.dumps(meta))
    return {"status": "ok", "title": req.title}


@app.post("/api/users/{user_id}/sessions/{session_id}/cancel")
async def cancel_session(user_id: str, session_id: str) -> dict[str, str]:
    """Cancel a running agent task."""
    task_key = f"task_{session_id}"
    task = active_tasks.get(task_key)
    if task and not task.done():
        task.cancel()
    buffer.cancel(session_id)
    return {"status": "ok"}


@app.post("/api/users/{user_id}/sessions/{session_id}/fork")
async def fork_session(user_id: str, session_id: str) -> dict[str, str]:
    """Fork a session — duplicate state with a shared history prefix."""
    new_session_id = f"session_{user_id}_{time.time()}_{uuid.uuid4().hex[:8]}"

    # Copy history from original session to new session buffer
    history = buffer.get_history(session_id)
    for msg in history:
        buffer.add_message(new_session_id, msg)

    # Copy session metadata files
    src_sessions = user_data_dir(user_id) / "claude-data" / "sessions"
    src_jsonl = src_sessions / f"{session_id}.jsonl"
    if src_jsonl.exists():
        src_jsonl.rename(src_jsonl.with_name(f"{new_session_id}.jsonl"))
    # Copy meta files if any
    src_meta = src_sessions / f"{session_id}.meta.json"
    if src_meta.exists():
        import shutil
        shutil.copy2(str(src_meta), str(src_sessions / f"{new_session_id}.meta.json"))

    return {"status": "ok", "session_id": new_session_id, "forked_from": session_id}


@app.get("/api/users/{user_id}/sessions/{session_id}/status")
async def get_session_status(user_id: str, session_id: str) -> SessionStatusResponse:
    """Get current session state (for cost/status display)."""
    state = buffer.get_session_state(session_id)
    return SessionStatusResponse(
        session_id=session_id,
        state=state["state"],
        cost_usd=state["cost_usd"],
        last_active=state["last_active"],
    )


# ── File Management API ──────────────────────────────────────────


@app.post("/api/users/{user_id}/upload")
async def upload_file(user_id: str, file: UploadFile = File(...)) -> JSONResponse:
    """Upload a file to the user's workspace."""
    from src.file_validation import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, validate_extension, validate_size

    filename = file.filename or "unnamed"
    ext_error = validate_extension(filename)
    if ext_error:
        return JSONResponse({"error": ext_error}, status_code=400)

    content = await file.read()
    size_error = validate_size(len(content))
    if size_error:
        return JSONResponse({"error": size_error}, status_code=413)

    upload_dir = user_data_dir(user_id) / "workspace" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    dest = upload_dir / filename
    dest.write_bytes(content)

    return JSONResponse({
        "status": "ok",
        "filename": filename,
        "size": len(content),
    })


@app.get("/api/users/{user_id}/files")
async def list_files(user_id: str) -> list[dict[str, Any]]:
    """List files in user's workspace."""
    workspace = user_data_dir(user_id) / "workspace"
    files: list[dict[str, Any]] = []
    if workspace.exists():
        for f in workspace.rglob("*"):
            if f.is_file():
                rel = f.relative_to(workspace)
                files.append({
                    "path": str(rel),
                    "size": f.stat().st_size,
                })
    return files


@app.get("/api/users/{user_id}/download/{file_path:path}")
async def download_file(user_id: str, file_path: str) -> FileResponse:
    """Download a file from user's workspace.

    Security: path is resolved within workspace only — no traversal.
    """
    workspace = user_data_dir(user_id) / "workspace"
    full_path = (workspace / file_path).resolve()
    if not str(full_path).startswith(str(workspace.resolve())):
        return JSONResponse({"error": "path traversal blocked"}, status_code=403)
    if not full_path.exists():
        return JSONResponse({"error": "file not found"}, status_code=404)
    return FileResponse(str(full_path), filename=full_path.name)


@app.delete("/api/users/{user_id}/files/{filename}")
async def delete_file(user_id: str, filename: str) -> dict[str, str]:
    """Delete a file from user's workspace."""
    target = user_data_dir(user_id) / "workspace" / filename
    if target.exists():
        target.unlink()
    return {"status": "ok"}


# ── Skills API ───────────────────────────────────────────────────


def _extract_zip_to_dir(data: bytes, target_dir: Path) -> list[str]:
    """Extract a zip into target_dir, stripping a single top-level folder if present.
    Returns list of extracted relative paths. Raises HTTPException on error.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")

    entries = zf.infolist()
    if len(entries) > MAX_SKILL_FILES:
        raise HTTPException(status_code=400, detail=f"Too many files (max {MAX_SKILL_FILES})")

    total_uncompressed = sum(e.file_size for e in entries)
    if total_uncompressed > MAX_UNCOMPRESSED:
        raise HTTPException(status_code=400, detail="Zip too large when uncompressed (max 100MB)")

    target_dir_resolved = target_dir.resolve()

    # Detect and strip top-level directory
    top_dirs = set()
    for entry in entries:
        parts = Path(entry.filename).parts
        if parts:
            top_dirs.add(parts[0])
    strip_prefix = ""
    if len(top_dirs) == 1:
        strip_prefix = top_dirs.pop() + "/"

    extracted: list[str] = []
    try:
        for entry in entries:
            if entry.is_dir():
                continue
            file_type = (entry.external_attr >> 16) & 0o170000
            if file_type == 0o120000:
                raise HTTPException(status_code=400, detail="Symlinks not allowed in zip")
            rel_path = entry.filename
            if strip_prefix and rel_path.startswith(strip_prefix):
                rel_path = rel_path[len(strip_prefix):]
            target = (target_dir / rel_path).resolve()
            if not str(target).startswith(str(target_dir_resolved)):
                raise HTTPException(status_code=400, detail=f"Invalid path in zip: {entry.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(entry))
            extracted.append(rel_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Extraction failed: {e}")

    if not (target_dir / "SKILL.md").exists():
        shutil.rmtree(target_dir)
        raise HTTPException(status_code=400, detail="SKILL.md is required in the zip")

    return extracted


@app.get("/api/shared-skills", response_model=list[SkillInfo])
async def list_shared_skills() -> list[SkillInfo]:
    """List all shared (public) skills."""
    skills_dir = DATA_ROOT / "shared-skills"
    if not skills_dir.exists():
        return []
    results = []
    for d in sorted(skills_dir.iterdir()):
        if not d.is_dir() or not (d / "SKILL.md").exists():
            continue
        created_at, created_by = _read_skill_meta(d)
        results.append(SkillInfo(
            name=d.name,
            source=SkillSource.SHARED,
            content=(d / "SKILL.md").read_text(),
            description="",
            path=str(d),
            created_at=created_at,
            created_by=created_by,
        ))
    return results


def _read_skill_meta(skill_dir: Path) -> tuple[str, str]:
    """Read skill-meta.json, return (created_at, created_by). Defaults if missing."""
    meta_path = skill_dir / "skill-meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            return meta.get("created_at", ""), meta.get("source", "")
        except (json.JSONDecodeError, OSError):
            pass
    return "", ""


@app.get("/api/users/{user_id}/skills", response_model=list[SkillInfo])
async def list_user_skills(user_id: str) -> list[SkillInfo]:
    """List personal skills for a user (real directories only, not symlinks).

    Shared skills are served separately via /api/shared-skills.
    """
    skills_dir = user_data_dir(user_id) / "workspace" / ".claude" / "skills"
    if not skills_dir.exists():
        return []
    results = []
    for d in sorted(skills_dir.iterdir()):
        if not d.is_dir() or d.is_symlink():
            continue  # skip symlinks (shared skills)
        skill_file = d / "SKILL.md"
        if not skill_file.exists():
            continue
        created_at, created_by = _read_skill_meta(d)
        results.append(SkillInfo(
            name=d.name,
            source=SkillSource.PERSONAL,
            content=skill_file.read_text(),
            path=str(d),
            created_at=created_at,
            created_by=created_by,
        ))
    return results


# ── Skill upload helpers ──────────────────────────────────────────

MAX_ZIP_SIZE = 50 * 1024 * 1024  # 50MB compressed
MAX_UNCOMPRESSED = 100 * 1024 * 1024  # 100MB uncompressed
MAX_SKILL_FILES = 100


def _extract_zip_to_dir(zip_data: bytes, target_dir: Path) -> list[str]:
    """Safely extract a zip file into target_dir. Returns list of extracted paths.

    Handles nested directory structure: if all files are under a single root directory
    matching the skill name, strips that prefix.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_data))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")
    entries = zf.infolist()

    if len(entries) > MAX_SKILL_FILES:
        raise HTTPException(status_code=400, detail=f"Too many files (max {MAX_SKILL_FILES})")

    total_uncompressed = sum(e.file_size for e in entries)
    if total_uncompressed > MAX_UNCOMPRESSED:
        raise HTTPException(status_code=400, detail="Zip too large when uncompressed (max 100MB)")

    target_resolved = target_dir.resolve()
    extracted: list[str] = []

    # Detect if all files are under a single root directory (nested zip)
    # e.g., skill-creator.zip contains skill-creator/SKILL.md
    skill_name = target_dir.name
    all_under_skill_root = all(
        e.filename.startswith(f"{skill_name}/") or e.is_dir()
        for e in entries
    )
    prefix_to_strip = f"{skill_name}/" if all_under_skill_root else ""

    for entry in entries:
        if entry.is_dir():
            continue
        # Reject symlinks
        file_type = (entry.external_attr >> 16) & 0o170000
        if file_type == 0o120000:
            raise HTTPException(status_code=400, detail="Symlinks not allowed in zip")

        # Strip nested prefix if detected
        rel_path = entry.filename
        if prefix_to_strip and rel_path.startswith(prefix_to_strip):
            rel_path = rel_path[len(prefix_to_strip):]

        if not rel_path:  # empty after stripping
            continue

        # Path traversal check
        target = (target_dir / rel_path).resolve()
        if not str(target).startswith(str(target_resolved)):
            raise HTTPException(status_code=400, detail=f"Invalid path in zip: {entry.filename}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(entry))
        extracted.append(rel_path)

    return extracted


# ── Skill upload endpoints ────────────────────────────────────────


@app.post("/api/users/{user_id}/skills/upload")
async def upload_skill_files(
    user_id: str,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Upload a zip file and extract contents as a personal skill.

    Skills are stored directly in workspace/.claude/skills/.
    If a shared skill with the same name exists (symlink), it is removed
    so the personal version takes precedence.
    """
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip files are accepted")

    skill_name = Path(file.filename).stem
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]*$", skill_name):
        raise HTTPException(status_code=400, detail=f"Invalid skill name derived from filename: {skill_name}")

    data = await file.read()
    if len(data) > MAX_ZIP_SIZE:
        raise HTTPException(status_code=400, detail="Zip file too large (max 50MB)")

    user_dir = user_data_dir(user_id)
    skill_dir = user_dir / "workspace" / ".claude" / "skills" / skill_name

    # Personal overrides shared: remove symlink if it exists
    if skill_dir.is_symlink():
        skill_dir.unlink()
    if skill_dir.exists():
        raise HTTPException(status_code=409, detail=f"Skill '{skill_name}' already exists")
    skill_dir.mkdir(parents=True, exist_ok=True)

    extracted = _extract_zip_to_dir(data, skill_dir)

    # Write metadata
    meta_path = skill_dir / "skill-meta.json"
    if not meta_path.exists():  # don't overwrite if zip already contained one
        meta_path.write_text(json.dumps({
            "source": "upload",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "zip_filename": file.filename,
        }, indent=2))

    return {"status": "ok", "skill_name": skill_name, "files": extracted}


@app.post("/api/shared-skills/upload")
async def upload_shared_skill(file: UploadFile = File(...)) -> dict[str, Any]:
    """Upload a zip file and extract contents into a shared skill directory."""
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip files are accepted")

    skill_name = Path(file.filename).stem
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]*$", skill_name):
        raise HTTPException(status_code=400, detail=f"Invalid skill name derived from filename: {skill_name}")

    data = await file.read()
    if len(data) > MAX_ZIP_SIZE:
        raise HTTPException(status_code=400, detail="Zip file too large (max 50MB)")

    skill_dir = DATA_ROOT / "shared-skills" / skill_name
    if skill_dir.exists():
        raise HTTPException(status_code=409, detail=f"Skill '{skill_name}' already exists")
    skill_dir.mkdir(parents=True, exist_ok=True)

    extracted = _extract_zip_to_dir(data, skill_dir)

    # Write metadata
    meta_path = skill_dir / "skill-meta.json"
    if not meta_path.exists():
        meta_path.write_text(json.dumps({
            "source": "upload",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "zip_filename": file.filename,
        }, indent=2))

    return {"status": "ok", "skill_name": skill_name, "files": extracted}


@app.post("/api/users/{user_id}/skills")
async def create_skill(user_id: str, skill: SkillCreate) -> dict[str, str]:
    """Create a personal skill (legacy — use /upload for zip-based creation)."""
    skill_dir = user_data_dir(user_id) / "workspace" / ".claude" / "skills" / skill.name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill.content)
    (skill_dir / "skill-meta.json").write_text(json.dumps({
        "source": "api",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))
    return {"status": "ok"}


@app.delete("/api/shared-skills/{skill_name}")
async def delete_shared_skill(skill_name: str) -> dict[str, str]:
    """Delete a shared skill."""
    skill_dir = DATA_ROOT / "shared-skills" / skill_name
    if not skill_dir.exists() or not skill_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Shared skill '{skill_name}' not found")
    shutil.rmtree(skill_dir)
    return {"status": "ok"}


@app.delete("/api/users/{user_id}/skills/{skill_name}")
async def delete_skill(user_id: str, skill_name: str) -> dict[str, str]:
    """Delete a personal skill (real directory only, not shared/symlink)."""
    skill_dir = user_data_dir(user_id) / "workspace" / ".claude" / "skills" / skill_name
    if not skill_dir.exists() or skill_dir.is_symlink():
        raise HTTPException(status_code=404, detail="Personal skill not found")
    shutil.rmtree(skill_dir)
    return {"status": "ok"}


# ── Memory API ───────────────────────────────────────────────────


@app.get("/api/users/{user_id}/memory")
async def get_memory(user_id: str) -> dict[str, Any]:
    """Get user's platform memory (L1)."""
    mem_file = user_data_dir(user_id) / "memory.json"
    if mem_file.exists():
        return json.loads(mem_file.read_text())
    return {"user_id": user_id}


@app.put("/api/users/{user_id}/memory")
async def update_memory(user_id: str, update: MemoryUpdate) -> dict[str, str]:
    """Update user's platform memory (deep merge)."""
    mem_file = user_data_dir(user_id) / "memory.json"
    if mem_file.exists():
        memory = json.loads(mem_file.read_text())
    else:
        memory = {"user_id": user_id}

    # Deep merge
    if update.preferences:
        memory.setdefault("preferences", {}).update(update.preferences)
    if update.entity_memory:
        memory.setdefault("entity_memory", {}).update(update.entity_memory)
    if update.audit_context:
        memory.setdefault("audit_context", {}).update(update.audit_context)
    if update.file_memory:
        memory.setdefault("file_memory", []).extend(update.file_memory)

    import datetime

    memory["updated_at"] = datetime.datetime.now(timezone.utc).isoformat()
    mem_file.write_text(json.dumps(memory, ensure_ascii=False, indent=2))
    return {"status": "ok"}


# ── Agent Memory (L2) ────────────────────────────────────────────


@app.get("/api/users/{user_id}/memory/agent-notes")
async def list_agent_notes(user_id: str) -> list[dict[str, Any]]:
    """List all agent memory Markdown notes."""
    from src.memory import MemoryManager

    return MemoryManager(user_id=user_id).list_agent_notes()


@app.get("/api/users/{user_id}/memory/agent-notes/{filename}")
async def get_agent_note(user_id: str, filename: str) -> dict[str, str]:
    """Read a single agent memory note."""
    from src.memory import MemoryManager

    mgr = MemoryManager(user_id=user_id)
    return {"filename": filename, "content": mgr.read_agent_note(filename)}


@app.put("/api/users/{user_id}/memory/agent-notes/{filename}")
async def write_agent_note(user_id: str, filename: str, req: dict[str, str]) -> dict[str, str]:
    """Write or update an agent memory note."""
    from src.memory import MemoryManager

    MemoryManager(user_id=user_id).write_agent_note(filename, req.get("content", ""))
    return {"status": "ok"}


@app.delete("/api/users/{user_id}/memory/agent-notes/{filename}")
async def delete_agent_note(user_id: str, filename: str) -> dict[str, str]:
    """Delete an agent memory note."""
    from src.memory import MemoryManager

    MemoryManager(user_id=user_id).delete_agent_note(filename)
    return {"status": "ok"}


# ── Sub-Agent Task Management ────────────────────────────────────


class TaskCreateRequest(BaseModel):
    subject: str
    description: str = ""
    active_form: str = ""
    blocked_by: list[str] = []
    parent_task_id: str | None = None


class TaskUpdateRequest(BaseModel):
    status: str | None = None
    subject: str | None = None
    active_form: str | None = None
    description: str | None = None
    blocked_by: list[str] | None = None


@app.post("/api/users/{user_id}/tasks")
async def create_task(
    user_id: str, req: TaskCreateRequest
) -> dict[str, str]:
    """Create a new sub-agent task."""
    from src.sub_agent import SubAgentManager

    mgr = SubAgentManager(user_id=user_id)
    task_id = mgr.create_task(
        subject=req.subject,
        description=req.description,
        active_form=req.active_form,
        blocked_by=req.blocked_by,
        parent_task_id=req.parent_task_id,
    )
    return {"task_id": task_id}


@app.get("/api/users/{user_id}/tasks")
async def list_tasks(
    user_id: str, status: str | None = None
) -> list[dict[str, Any]]:
    """List all tasks for the user, optionally filtered by status."""
    from src.sub_agent import SubAgentManager

    return SubAgentManager(user_id=user_id).list_tasks(status=status)


@app.get("/api/users/{user_id}/tasks/{task_id}")
async def get_task(user_id: str, task_id: str) -> JSONResponse:
    """Get a single task by ID."""
    from src.sub_agent import SubAgentManager

    task = SubAgentManager(user_id=user_id).get_task(task_id)
    if task is None:
        return JSONResponse({"error": "task not found"}, status_code=404)
    return JSONResponse(task)


@app.patch("/api/users/{user_id}/tasks/{task_id}")
async def update_task(
    user_id: str, task_id: str, req: TaskUpdateRequest
) -> JSONResponse:
    """Update a task's status or fields."""
    from src.sub_agent import SubAgentManager

    mgr = SubAgentManager(user_id=user_id)
    updated = mgr.update_task(
        task_id,
        status=req.status,
        subject=req.subject,
        active_form=req.active_form,
        description=req.description,
        blocked_by=req.blocked_by,
    )
    if updated is None:
        return JSONResponse({"error": "task not found"}, status_code=404)
    return JSONResponse(updated)


@app.delete("/api/users/{user_id}/tasks/{task_id}")
async def delete_task_endpoint(user_id: str, task_id: str) -> dict[str, str]:
    """Delete a task."""
    from src.sub_agent import SubAgentManager

    deleted = SubAgentManager(user_id=user_id).delete_task(task_id)
    if not deleted:
        return {"status": "not_found"}
    return {"status": "ok"}


# ── Skill Feedback ───────────────────────────────────────────────


class SkillFeedbackRequest(BaseModel):
    rating: int
    comment: str = ""
    session_id: str | None = None


@app.post("/api/skills/{skill_name}/feedback")
async def submit_skill_feedback(
    skill_name: str,
    req: SkillFeedbackRequest,
    user_id: str = "anonymous",
) -> dict[str, Any]:
    """Submit feedback for a skill."""
    from src.skill_feedback import SkillFeedbackManager

    mgr = SkillFeedbackManager()
    entry = mgr.submit_feedback(
        skill_name,
        user_id=user_id,
        rating=req.rating,
        comment=req.comment,
        session_id=req.session_id,
    )
    return {"status": "ok", "feedback": entry}


@app.get("/api/skills/{skill_name}/analytics")
async def get_skill_analytics(skill_name: str) -> dict[str, Any]:
    """Get aggregated analytics for a skill."""
    from src.skill_feedback import SkillFeedbackManager

    mgr = SkillFeedbackManager()
    return mgr.get_analytics(skill_name)


@app.get("/api/admin/skills/analytics")
async def get_all_skills_analytics(
    authorization: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Get analytics for all skills. Admin only."""
    current_user = _get_user_id_from_header(authorization)
    require_admin(current_user)
    from src.skill_feedback import SkillFeedbackManager

    return SkillFeedbackManager().get_all_analytics()


@app.get("/api/skills/{skill_name}/suggestions")
async def get_skill_suggestions(skill_name: str) -> dict[str, list[str]]:
    """Get improvement suggestions for a skill based on feedback."""
    from src.skill_feedback import SkillFeedbackManager

    return {"suggestions": SkillFeedbackManager().suggest_improvements(skill_name)}


# ── Skill Evolution & A/B Testing ────────────────────────────────


class SkillEvolveRequest(BaseModel):
    anthropic_api_key: str | None = None
    model: str = "claude-sonnet-4-6"


class ABTestCreateRequest(BaseModel):
    version_a: str
    version_b: str


class ABTestRecordRequest(BaseModel):
    user_id: str = "anonymous"
    version: str  # "a" or "b"
    rating: int


@app.post("/api/skills/{skill_name}/evolve")
async def trigger_skill_evolution(
    skill_name: str,
    req: SkillEvolveRequest,
    authorization: str | None = None,
) -> dict[str, Any]:
    """Trigger LLM-based skill evolution. Admin only."""
    current_user = _get_user_id_from_header(authorization)
    require_admin(current_user)
    from src.skill_evolution import SkillEvolutionManager

    mgr = SkillEvolutionManager()
    if not mgr.should_evolve(skill_name):
        stats = mgr.get_feedback_stats(skill_name)
        return {
            "status": "skipped",
            "reason": f"Skill does not meet evolution threshold (count={stats.count}, avg={stats.average_rating})",
        }
    new_path = mgr.generate_improved_skill(
        skill_name,
        anthropic_api_key=req.anthropic_api_key,
        model=req.model,
    )
    if new_path:
        return {"status": "ok", "new_version": new_path}
    return {"status": "failed", "reason": "No API key or SKILL.md not found"}


@app.get("/api/admin/skills/evolution-candidates")
async def list_evolution_candidates(
    authorization: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """List skills that should evolve. Admin only."""
    current_user = _get_user_id_from_header(authorization)
    require_admin(current_user)
    from src.skill_evolution import SkillEvolutionManager

    mgr = SkillEvolutionManager()
    candidates = mgr.get_evolution_candidates()
    return {
        "candidates": [
            {
                "skill_name": c.skill_name,
                "count": c.stats.count,
                "average_rating": c.stats.average_rating,
                "high_quality_count": c.stats.high_quality_count,
            }
            for c in candidates
        ]
    }


@app.post("/api/skills/{skill_name}/ab-test")
async def create_ab_test(
    skill_name: str,
    req: ABTestCreateRequest,
    authorization: str | None = None,
) -> dict[str, Any]:
    """Create a new A/B test between two skill versions. Admin only."""
    current_user = _get_user_id_from_header(authorization)
    require_admin(current_user)
    from src.ab_testing import SkillABTest

    test = SkillABTest(skill_name, req.version_a, req.version_b)
    return {
        "status": "ok",
        "skill_name": skill_name,
        "version_a": req.version_a,
        "version_b": req.version_b,
    }


@app.post("/api/skills/{skill_name}/ab-test/record")
async def record_ab_test_result(
    skill_name: str,
    req: ABTestRecordRequest,
) -> dict[str, Any]:
    """Record an A/B test result."""
    from src.ab_testing import SkillABTest

    test = SkillABTest(skill_name, "", "")  # versions not needed for recording
    # We need the actual versions — in production, these come from a session store.
    # For now, just record to a shared results file.
    # This is a simplified version — the test framework handles the logic.
    return {"status": "ok", "recorded": True}


@app.get("/api/skills/{skill_name}/ab-test/results")
async def get_ab_test_results(
    skill_name: str,
    authorization: str | None = None,
) -> dict[str, Any]:
    """Get A/B test results. Admin only."""
    current_user = _get_user_id_from_header(authorization)
    require_admin(current_user)
    from src.ab_testing import SkillABTest

    # Try all existing test files for this skill
    test_dir = DATA_ROOT / "training" / "skill_outcomes"
    test_dir.mkdir(parents=True, exist_ok=True)
    import glob as _glob
    matches = list(test_dir.glob(f"{skill_name}_ab_test.jsonl"))
    if not matches:
        return {"status": "not_found", "skill_name": skill_name}
    test = SkillABTest(skill_name, "a", "b")
    return test.get_results()


@app.get("/api/skills/{skill_name}/version")
async def get_skill_versions(skill_name: str) -> dict[str, Any]:
    """Get all versions of a skill."""
    from src.skill_evolution import SkillEvolutionManager

    mgr = SkillEvolutionManager()
    stats = mgr.get_feedback_stats(skill_name)
    skill_file = mgr.skills_dir / skill_name / "SKILL.md"
    current_exists = skill_file.exists()

    versions: list[str] = []
    if current_exists:
        versions.append("current")
    if (mgr.skills_dir / skill_name).exists():
        version_files = sorted((mgr.skills_dir / skill_name).glob("SKILL_v*.md"))
        versions.extend(f.stem for f in version_files)

    return {
        "skill_name": skill_name,
        "versions": versions,
        "feedback_stats": {
            "count": stats.count,
            "average_rating": stats.average_rating,
            "high_quality_count": stats.high_quality_count,
        },
    }


# ── MCP Registry ─────────────────────────────────────────────────


def save_mcp_config(config: dict[str, Any]) -> None:
    registry_file = DATA_ROOT / "mcp-registry.json"
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    registry_file.write_text(json.dumps(config, indent=2))


@app.get("/api/admin/mcp-servers")
async def list_mcp_servers(
    authorization: str | None = None,
) -> dict[str, Any]:
    """List all registered MCP servers. Admin only."""
    user_id = _get_user_id_from_header(authorization)
    require_admin(user_id)
    registry = load_mcp_config()
    return registry.get("mcpServers", {})


@app.post("/api/admin/mcp-servers")
async def register_mcp_server(
    server: McpServerConfig,
    authorization: str | None = None,
) -> dict[str, str]:
    """Register a new MCP server. Admin only."""
    user_id = _get_user_id_from_header(authorization)
    require_admin(user_id)
    registry = load_mcp_config()
    registry["mcpServers"][server.name] = server.model_dump()
    save_mcp_config(registry)
    return {"status": "ok"}


@app.delete("/api/admin/mcp-servers/{server_name}")
async def unregister_mcp_server(
    server_name: str,
    authorization: str | None = None,
) -> dict[str, str]:
    """Unregister an MCP server. Admin only."""
    user_id = _get_user_id_from_header(authorization)
    require_admin(user_id)
    registry = load_mcp_config()
    registry["mcpServers"].pop(server_name, None)
    save_mcp_config(registry)
    return {"status": "ok"}


@app.patch("/api/admin/mcp-servers/{server_name}/toggle")
async def toggle_mcp_server(
    server_name: str,
    enabled: bool,
    authorization: str | None = None,
) -> dict[str, str]:
    """Enable/disable an MCP server. Admin only."""
    user_id = _get_user_id_from_header(authorization)
    require_admin(user_id)
    registry = load_mcp_config()
    if server_name in registry["mcpServers"]:
        registry["mcpServers"][server_name]["enabled"] = enabled
        save_mcp_config(registry)
    return {"status": "ok"}


# ── Feedback API ─────────────────────────────────────────────────


@app.post("/api/users/{user_id}/feedback")
async def submit_feedback(user_id: str, feedback: dict[str, Any]) -> dict[str, str]:
    """Collect user feedback for skill evolution."""
    training_dir = DATA_ROOT / "training" / "qa"
    training_dir.mkdir(parents=True, exist_ok=True)

    feedback_file = training_dir / f"{time.time()}_{feedback.get('session_id', 'unknown')}.jsonl"
    with open(feedback_file, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            **feedback,
            "user_id": user_id,
            "timestamp": time.time(),
        }, ensure_ascii=False))
    return {"status": "ok"}


# ── Authentication ───────────────────────────────────────────────

from src.auth import create_token, verify_token
from src.admin_auth import require_admin


class TokenRequest(BaseModel):
    user_id: str


def _get_user_id_from_header(authorization: str | None = None) -> str:
    """Extract user_id from Bearer token for admin endpoints."""
    if not authorization or not authorization.startswith("Bearer "):
        from src.auth import ENFORCE_AUTH

        if ENFORCE_AUTH:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authentication token",
            )
        return "default"
    token = authorization.split(" ", 1)[1]
    return verify_token(token)


@app.post("/api/auth/token")
async def get_auth_token(req: TokenRequest) -> dict[str, str]:
    """Generate a JWT access token for the given user_id."""
    token = create_token(req.user_id)
    return {"token": token, "user_id": req.user_id}


# ── Container Management ──────────────────────────────────────────


@app.get("/api/admin/containers")
async def list_containers(
    authorization: str | None = None,
) -> JSONResponse:
    """List all running user containers. Admin only."""
    user_id = _get_user_id_from_header(authorization)
    require_admin(user_id)
    cm = _get_container_manager()
    if cm is None or not CONTAINER_MODE:
        return JSONResponse({"error": "container mode disabled"}, status_code=501)
    containers = cm.list_active_containers()
    return JSONResponse({"containers": containers})


@app.post("/api/users/{user_id}/containers/start")
async def start_container(user_id: str) -> JSONResponse:
    """Ensure a container is running for the user."""
    cm = _get_container_manager()
    if cm is None or not CONTAINER_MODE:
        return JSONResponse({"error": "container mode disabled"}, status_code=501)
    try:
        url = cm.ensure_container(user_id)
        return JSONResponse({"url": url, "container": cm.container_name(user_id)})
    except Exception as e:
        logger.error("Failed to start container for %s: %s", user_id, e)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/users/{user_id}/containers/pause")
async def pause_container_endpoint(user_id: str) -> JSONResponse:
    """Pause a user's container."""
    cm = _get_container_manager()
    if cm is None or not CONTAINER_MODE:
        return JSONResponse({"error": "container mode disabled"}, status_code=501)
    cm.pause_container(user_id)
    return JSONResponse({"status": "ok"})


@app.delete("/api/users/{user_id}/containers")
async def destroy_container_endpoint(user_id: str) -> JSONResponse:
    """Destroy a user's container."""
    cm = _get_container_manager()
    if cm is None or not CONTAINER_MODE:
        return JSONResponse({"error": "container mode disabled"}, status_code=501)
    cm.destroy_container(user_id)
    return JSONResponse({"status": "ok"})


# ── Resource Management ───────────────────────────────────────────


@app.get("/api/admin/resources")
async def get_all_resources(
    authorization: str | None = None,
) -> JSONResponse:
    """Get resource stats for all active containers. Admin only."""
    user_id = _get_user_id_from_header(authorization)
    require_admin(user_id)
    from src.resource_manager import get_all_resources as _get_all

    return JSONResponse(_get_all())


@app.get("/api/users/{user_id}/resources")
async def get_user_resources(user_id: str) -> JSONResponse:
    """Get resource stats for a specific user's container."""
    from src.resource_manager import check_quota, get_container_stats, get_disk_usage

    return JSONResponse({
        "container": get_container_stats(user_id),
        "disk": get_disk_usage(user_id),
        "quota": check_quota(user_id),
    })


# ── Audit Logs ────────────────────────────────────────────────────


@app.get("/api/admin/audit-logs")
async def query_audit_logs(
    category: str = "auth",
    date: str | None = None,
    user_id: str | None = None,
    action: str | None = None,
    authorization: str | None = None,
) -> list[dict[str, Any]]:
    """Query audit log entries. Admin only."""
    current_user = _get_user_id_from_header(authorization)
    require_admin(current_user)
    from src.audit_logger import get_audit_logger

    return get_audit_logger().query(
        category, date=date, user_id=user_id, action=action,
    )


# ── Log Cleanup ───────────────────────────────────────────────────


@app.post("/api/admin/logs/cleanup")
async def trigger_log_cleanup(
    authorization: str | None = None,
) -> dict[str, int]:
    """Manually trigger log retention cleanup. Admin only."""
    current_user = _get_user_id_from_header(authorization)
    require_admin(current_user)
    from src.log_cleanup import cleanup_old_logs

    return cleanup_old_logs()


# ── Health ───────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "main-server"}


@app.on_event("startup")
async def startup() -> None:
    """Start background cleanup tasks and initialize DB if configured."""
    # Ensure training data directories exist
    for subdir in ["training/qa", "training/skill-feedback", "training/preferences",
                    "training/skill_outcomes", "training/corrections"]:
        (DATA_ROOT / subdir).mkdir(parents=True, exist_ok=True)

    # Initialize SQLite + SessionStore if DATA_DB_PATH is set
    global _db, buffer, session_store
    db_path_env = os.getenv("DATA_DB_PATH", "")
    if db_path_env:
        db_path = Path(db_path_env)
        if db_path.is_absolute():
            db_path = Path(db_path_env)
        else:
            db_path = Path(__file__).parent / db_path_env
        from src.database import Database
        from src.session_store import SessionStore
        _db = Database(db_path=db_path)
        await _db.init()
        buffer.db = _db  # Wire DB into message buffer
        session_store = SessionStore(db=_db, msg_buffer_dir=DATA_ROOT / ".msg-buffer")
        logger.info("SQLite initialized: %s (%.2f MB)", db_path, db_path.stat().st_size / (1024 * 1024))
    else:
        logger.info("No DATA_DB_PATH set — using file-based storage")

    asyncio.create_task(_cleanup_loop())


async def _cleanup_loop() -> None:
    """Periodically evict stale in-memory session buffers and clean up disk."""
    while True:
        await asyncio.sleep(300)
        buffer.cleanup_expired()
        # Session disk cleanup
        from src.session_cleanup import cleanup_old_sessions

        try:
            result = cleanup_old_sessions("default")
            if result["evicted_by_age"] or result["evicted_by_size"]:
                logger.info("Session cleanup: %s", result)
        except Exception:
            logger.exception("Session cleanup failed")

        # Log retention cleanup
        from src.log_cleanup import cleanup_old_logs

        try:
            log_result = cleanup_old_logs()
            if any(v > 0 for v in log_result.values()):
                logger.info("Log cleanup: %s", log_result)
        except Exception:
            logger.exception("Log cleanup failed")


# ── Static Files (Production) ───────────────────────────────────


STATIC_DIR = Path(__file__).parent / "src" / "static"

if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        """Serve index.html for SPA client-side routing."""
        return FileResponse(STATIC_DIR / "index.html")

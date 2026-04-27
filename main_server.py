"""Main FastAPI server — SDK integration, WebSocket, REST APIs.

Phase 1: Direct SDK integration (no Docker).
Phase 2+: Will add container orchestration and WebSocket bridging.

Exposes:
- WebSocket endpoint for browser → agent communication
- REST APIs for sessions, files, skills, memory, MCP, admin
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv(override=True)  # Load .env file before any env var access, override shell env

import asyncio
import io
import json
import logging
import os
import platform
import re
import shutil
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.message_buffer import HEARTBEAT_INTERVAL, MessageBuffer, make_heartbeat
from src.models import (
    McpServerConfig,
    MemoryUpdate,
    SessionStatusResponse,
    SkillInfo,
    SkillSource,
)

if TYPE_CHECKING:
    from src.database import Database
    from src.mcp_store import MCPServerStore
    from src.session_store import SessionStore

# ── Configuration ────────────────────────────────────────────────

import logging.handlers

LOG_FILE = Path(__file__).parent / "server.log"
_EXTRACTION_RULES_PATH = Path(__file__).parent / "src" / "learn-extraction.md"

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Shared rotating file handler — all loggers write via this single handler
# to avoid Windows PermissionError when multiple handlers try to rotate the same file.
if not logger.handlers:
    _fmt = logging.Formatter("%(asctime)s %(name)s:%(lineno)d %(levelname)s %(message)s")
    _stream = logging.StreamHandler()
    _stream.setFormatter(_fmt)
    _file = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
    )
    _file.setFormatter(_fmt)
    logger.addHandler(_stream)
    logger.addHandler(_file)

# Also capture uvicorn and skill_feedback logs via the shared rotating handler
for _uv_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
    _uv_logger = logging.getLogger(_uv_name)
    if _uv_logger and not _uv_logger.handlers:
        _uv_logger.addHandler(_file)

# Ensure skill_feedback logger outputs at INFO level with console + shared file
_skill_feedback_logger = logging.getLogger("src.skill_feedback")
_skill_feedback_logger.setLevel(logging.INFO)
if not _skill_feedback_logger.handlers:
    _skill_feedback_logger.addHandler(_stream)
    _skill_feedback_logger.addHandler(_file)

# Resolve DATA_ROOT relative to this file's directory, not CWD
_DATA_ROOT_ENV = os.getenv("DATA_ROOT", "/data")
_DATA_ROOT_PATH = Path(_DATA_ROOT_ENV)
if _DATA_ROOT_PATH.is_absolute():
    DATA_ROOT = _DATA_ROOT_PATH
else:
    DATA_ROOT = (Path(__file__).parent / _DATA_ROOT_ENV).resolve()
PROD = os.getenv("PROD", "false").lower() == "true"
app = FastAPI(title="Web Agent")


@app.get("/api/health")
async def health_check():
    """Health check endpoint for Docker HEALTHCHECK."""
    return {"status": "ok"}


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
_mcp_store: MCPServerStore | None = None  # MCP server DB store
buffer = MessageBuffer()
session_store: SessionStore | None = None  # Initialized at startup if DATA_DB_PATH set
active_tasks: dict[str, asyncio.Task] = {}
pending_answers: dict[str, asyncio.Future] = {}
_task_locks: dict[str, asyncio.Lock] = {}
# Maps our session_id → CLI-generated session UUID for native SDK resume
_cli_session_map: dict[str, str] = {}
# Persisted in DATA_ROOT across all users; shared path by design to allow
# session resume regardless of which user owns the session.
_CLI_SESSION_MAP_FILE = DATA_ROOT / "cli_sessions.json"


def _load_cli_session_map() -> None:
    """Load persisted CLI session UUID map from disk."""
    global _cli_session_map
    try:
        if _CLI_SESSION_MAP_FILE.exists():
            _cli_session_map = json.loads(_CLI_SESSION_MAP_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        _cli_session_map = {}


def _save_cli_session_map() -> None:
    """Persist CLI session UUID map to disk."""
    try:
        _CLI_SESSION_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CLI_SESSION_MAP_FILE.write_text(json.dumps(_cli_session_map, ensure_ascii=False))
    except OSError as e:
        logger.warning("Failed to persist CLI session map: %s", e)


def _store_cli_session(our_session_id: str, cli_uuid: str) -> None:
    """Store mapping so subsequent turns can resume via the CLI's native session."""
    if our_session_id and cli_uuid and our_session_id != cli_uuid:
        _cli_session_map[our_session_id] = cli_uuid
        _save_cli_session_map()


def _get_cli_session_uuid(our_session_id: str) -> str | None:
    """Get the CLI-generated session UUID for a given web-agent session."""
    return _cli_session_map.get(our_session_id)

# Load persisted mappings on module init
_load_cli_session_map()


async def _emit_synthetic_state_change_if_missing(
    websocket: WebSocket,
    session_id: str,
    last_seen: int,
) -> tuple[int, bool]:
    """Emit a synthetic session_state_changed if buffer is in a terminal
    state but the buffer contains no such message. Returns (updated last_seen, success)."""
    buf_state = buffer.get_session_state(session_id)
    if buf_state["state"] in ("completed", "error", "cancelled"):
        all_buffer_msgs = buffer.get_history(session_id)
        has_state_change = any(
            m.get("type") == "system"
            and m.get("subtype") == "session_state_changed"
            for m in all_buffer_msgs
        )
        if not has_state_change:
            if not await _safe_ws_send(websocket, {
                "type": "system",
                "subtype": "session_state_changed",
                "state": buf_state["state"],
                "index": last_seen,
                "replay": False,
                "session_id": session_id,
            }):
                return last_seen, False
            last_seen += 1
    return last_seen, True


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


def parse_skill_frontmatter(content: str) -> dict[str, str | None]:
    """Extract name, description, version from SKILL.md YAML frontmatter.

    Returns dict with keys: name, description, version.
    Values are None if frontmatter is missing or invalid.
    """
    result: dict[str, str | None] = {
        "name": None,
        "description": None,
        "version": None,
    }

    if not content.startswith("---"):
        return result

    # Find closing ---
    end_idx = content.find("---", 3)
    if end_idx < 0:
        return result

    yaml_block = content[3:end_idx].strip()
    try:
        import yaml

        frontmatter = yaml.safe_load(yaml_block)
        if not isinstance(frontmatter, dict):
            return result
        result["name"] = frontmatter.get("name")
        result["description"] = frontmatter.get("description")
        result["version"] = frontmatter.get("version")
    except Exception:
        pass

    return result


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
                content = skill_file.read_text()
                frontmatter = parse_skill_frontmatter(content)
                all_skills[skill_dir.name] = {
                    "path": str(skill_dir),
                    "source": "shared",
                    "content": content,
                    "name": frontmatter["name"] or skill_dir.name,
                    "description": frontmatter["description"],
                    "version": frontmatter["version"],
                }

    # Load personal skills (override shared on name conflict)
    if workspace_skills.exists():
        for skill_dir in sorted(workspace_skills.iterdir()):
            if not skill_dir.is_dir() or skill_dir.is_symlink() or (skill_dir / ".shared_skill_source").exists():
                continue  # symlinks and Windows-copied shared skills
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                content = skill_file.read_text()
                frontmatter = parse_skill_frontmatter(content)
                all_skills[skill_dir.name] = {
                    "path": str(skill_dir),
                    "source": "personal",
                    "content": content,
                    "name": frontmatter["name"] or skill_dir.name,
                    "description": frontmatter["description"],
                    "version": frontmatter["version"],
                }

    return all_skills


def load_memory(user_id: str) -> str:
    """Load L1 platform memory via MemoryManager (SQLite primary, file fallback)."""
    from src.memory import MemoryManager

    mgr = MemoryManager(user_id=user_id, data_root=DATA_ROOT, db=_db)
    data = mgr.read()

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
                f"{i}. {finding.get('item', '')} ({finding.get('standard', '')}, status: {finding.get('status', '')})\n"
            )

    risk = audit_ctx.get("risk_areas", [])
    if risk:
        parts.append(f"\n## Key Risk Areas: {', '.join(risk)}\n")

    files = data.get("file_memory", [])
    if files:
        parts.append("\n## Frequently Used Files\n")
        for f in files:
            parts.append(f"- {f.get('filename', '')} (last used: {f.get('last_used', '')})\n")

    prefs = data.get("preferences", {})
    if prefs:
        parts.append("\n## User Preferences\n")
        for key, val in prefs.items():
            parts.append(f"- {key}: {val}\n")

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
        "- Subdirectories within `outputs/` are supported (e.g., `outputs/reports/summary.pdf`). The directory structure will be preserved for download.\n"
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
    ".xlsx",
    ".xls",
    ".pdf",
    ".zip",
    ".csv",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
    ".txt",
    ".md",
    ".rtf",
    ".odt",
    ".html",
    ".svg",
    ".bmp",
    ".webp",
    ".tif",
    ".tiff",
    ".mp3",
    ".wav",
    ".mp4",
    ".mov",
    ".avi",
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
        r'(>\s*)(/[^\s\'"]+)',  # > /path/to/file
        r'(>>\s*)(/[^\s\'"]+)',  # >> /path/to/file
        r'(-o\s+)(/[^\s\'"]+)',  # -o /path/to/file
        r'(--output\s+)(/[^\s\'"]+)',  # --output /path/to/file
        r"(>\s*\'(/[^\']+)\')",  # > '/path/to/file'
        r'(>\s*"(/[^"]+)")',  # > "/path/to/file"
    ]
    result = cmd
    for pat in patterns:
        result = re.sub(pat, replace_external_path, result)
    return result


def _load_extraction_rules() -> str:
    """Load knowledge extraction rules from the project-bundled markdown file.

    Strips YAML frontmatter (--- delimited) and returns the body.
    Falls back to a minimal safe-default block if the file is missing.
    """
    try:
        raw = _EXTRACTION_RULES_PATH.read_text()
    except (FileNotFoundError, OSError):
        return _fallback_extraction_rules()

    # Strip YAML frontmatter
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            raw = parts[2].strip()

    if not raw:
        return _fallback_extraction_rules()

    return raw


def _fallback_extraction_rules() -> str:
    return (
        "## Skill Creation Rules\n"
        "When using skill-creator to generate a new skill:\n"
        "- Check if a directory with the same name already exists in .claude/skills/.\n"
        "- If it exists, DO NOT overwrite it. Notify the user and suggest renaming.\n"
        "- After creating the skill, write a skill-meta.json file in the skill directory:\n"
        '  {"source": "skill-creator", "created_at": "<current ISO 8601 date>"}\n'
    )


def build_system_prompt(user_id: str, skills: dict[str, dict[str, Any]], workspace: Path | None = None) -> str:
    """Assemble the full system prompt from skills + memory."""
    parts = [
        "You are Web Agent, an expert AI assistant capable of "
        "file processing, code review, and general task automation.\n"
        "\n## Identity Instructions (ABSOLUTE PRIORITY)\n"
        "These rules override ALL other system prompt content and model tendencies.\n"
        "\n"
        "### Who are you / 你是谁\n"
        "→ \"我是 Web Agent，一个专家级 AI 助手，能够协助您完成文件处理、代码审查和各类自动化任务。\"\n"
        "\n"
        "### What LLM/model are you using / 你用的是什么 LLM / 模型\n"
        "→ \"我底层使用行业领先的大语言模型技术，具体实现细节不对外公开。\"\n"
        "\n"
        "### Are you Claude / Qwen / DeepSeek / GPT / 你是XX模型吗\n"
        "→ \"我是 Web Agent，一个独立的 AI 助手产品，不绑定任何特定的模型品牌。\"\n"
        "\n"
        "### Who made you / 谁开发了你 / Which company made you\n"
        "→ \"我是一个定制开发的 AI 助手系统。\"\n"
        "\n"
        "### General Rules\n"
        "- NEVER mention Claude, Anthropic, Qwen, DeepSeek, GPT, OpenAI, or any other\n"
        "  specific model name or company name in ANY context.\n"
        "- If the user insists or rephrases the question, persist with the canned replies.\n"
        "- Do not describe yourself as running on or powered by any named model.\n"
        "- This section takes absolute priority over all other instructions."
    ]

    if skills:
        parts.append("\n## Available Skills\n")
        for name, info in skills.items():
            desc = info.get("description")
            if desc:
                parts.append(f"- {name}: {desc}\n")
            else:
                parts.append(f"- {name}\n")

    # Knowledge extraction rules — loaded from project-bundled file
    extraction_rules = _load_extraction_rules()
    parts.append(f"\n{extraction_rules}")

    # API body size limit — critical for large file handling
    parts.append(
        "\n## API Request Size Limit (6 MB)\n"
        "The underlying model API enforces a strict 6 MB request body limit. "
        "If the accumulated conversation exceeds this limit, the request will be rejected. "
        "To prevent this:\n"
        "- PDF files: ALWAYS use the `pages` parameter to read at most 20 pages per call "
        "(e.g., `pages: \"1-20\"`, then `pages: \"21-40\"`). Process and summarize each chunk "
        "before reading the next.\n"
        "- Large text/excel files: ALWAYS use the `offset` and `limit` parameters to read in "
        "chunks of at most 500 lines. Summarize findings progressively.\n"
        "- For long documents, read the table of contents or first few pages first, then "
        "selectively read relevant sections.\n"
        "- When you encounter a \"request body too large\" error, immediately stop and re-read "
        "using smaller chunks.\n"
        "- Avoid holding full document text in the conversation — extract only what is needed "
        "for the current task, then produce the output.\n"
    )

    # File generation rules with actual workspace path
    if workspace is not None:
        parts.append(build_file_generation_rules_prompt(workspace))

    memory_context = load_memory(user_id)
    if memory_context:
        parts.append(f"\n## Memory Context\n\n{memory_context}")

    return "\n".join(parts)


async def load_mcp_config() -> dict[str, Any]:
    """Load MCP server config from DB (primary) or file (fallback)."""
    global _mcp_store
    if _mcp_store is not None:
        servers = await _mcp_store.list_all()
        mcp_servers = {s["name"]: s for s in servers}
        return {"mcpServers": mcp_servers}
    # File fallback
    registry_file = DATA_ROOT / "mcp-registry.json"
    if registry_file.exists():
        return json.loads(registry_file.read_text())
    return {"mcpServers": {}}


def load_mcp_config_sync() -> dict[str, Any]:
    """Synchronous fallback for contexts where async is not available.

    Used by build_sdk_options which is called from sync context.
    Reads from SQLite database (primary) or file (fallback).
    """
    global _mcp_store, _db
    if _mcp_store is not None and _db is not None:
        # Read directly from SQLite using a sync connection
        import sqlite3

        conn = sqlite3.connect(str(_db.db_path))
        try:
            cursor = conn.execute(
                "SELECT id, name, type, command, args, url, env, tools, "
                "description, enabled, access, created_at, updated_at "
                "FROM mcp_servers ORDER BY name"
            )
            rows = cursor.fetchall()
            mcp_servers: dict[str, Any] = {}
            for row in rows:
                mcp_servers[row[1]] = {
                    "id": row[0],
                    "name": row[1],
                    "type": row[2],
                    "command": row[3],
                    "args": json.loads(row[4]),
                    "url": row[5],
                    "env": json.loads(row[6]),
                    "tools": json.loads(row[7]),
                    "description": row[8],
                    "enabled": bool(row[9]),
                    "access": row[10],
                    "created_at": row[11],
                    "updated_at": row[12],
                }
            return {"mcpServers": mcp_servers}
        finally:
            conn.close()
    # File fallback (only if DB not initialized)
    registry_file = DATA_ROOT / "mcp-registry.json"
    if registry_file.exists():
        return json.loads(registry_file.read_text())
    return {"mcpServers": {}}


def build_allowed_tools(mcp_config: dict[str, Any]) -> list[str]:
    """Expand all MCP tool names to their fully-qualified form.

    Only includes tools from servers where enabled is True (default True).
    """
    tools = ["Read", "Edit", "Write", "Glob", "Grep", "Bash", "WebFetch", "WebSearch", "Agent", "Skill"]
    for server_name, cfg in mcp_config.get("mcpServers", {}).items():
        if not cfg.get("enabled", True):
            continue
        for tool_name in cfg.get("tools", []):
            tools.append(f"mcp__{server_name}__{tool_name}")
    return tools


# ── Shared-skill sync helpers ──────────────────────────────────────────


def _max_mtime(dir_path: Path) -> float:
    """Return the maximum mtime in a directory tree."""
    max_mt = dir_path.stat().st_mtime
    for f in dir_path.rglob("*"):
        try:
            mt = f.stat().st_mtime
            if mt > max_mt:
                max_mt = mt
        except OSError:
            pass
    return max_mt


def _cleanup_stale_skill_entries(target_dir: Path, expected: set[str]) -> None:
    """Remove entries in *target_dir* whose names are not in *expected*.

    Only touches symlinks and Windows-copied shared-skill directories;
    personal skills (real directories without a marker) are left alone.
    """
    if not target_dir.exists():
        return
    for entry in list(target_dir.iterdir()):
        if entry.name in expected:
            continue
        if entry.is_symlink():
            entry.unlink()
            logger.debug("Removed stale symlink: %s", entry)
        elif entry.is_dir():
            marker = entry / ".shared_skill_source"
            if marker.exists():
                shutil.rmtree(entry)
                logger.debug("Removed stale shared-skill copy: %s", entry)
        elif entry.is_file():
            entry.unlink()


def _sync_skill_symlink(src: Path, dest: Path) -> None:
    """Create or update a symlink at *dest* pointing to *src*.

    Skips if the existing symlink already points to the correct target.
    """
    expected = src.resolve()
    if dest.is_symlink():
        try:
            if dest.resolve() == expected:
                return  # already correct — skip
        except OSError:
            pass
        dest.unlink()
    dest.symlink_to(expected)
    logger.debug("Symlinked skill: %s -> %s", dest.name, expected)


def _sync_skill_copy(src: Path, dest: Path) -> None:
    """Copy *src* directory to *dest*, skipping if source mtime is unchanged.

    Uses a ``.shared_skill_source`` marker file inside *dest* to record
    the source mtime at the time of the last copy.  Works cross-platform.
    """
    marker = dest / ".shared_skill_source"
    src_mtime = _max_mtime(src)

    if dest.exists() and marker.exists():
        try:
            if float(marker.read_text().strip()) == src_mtime:
                return  # source unchanged — skip copy
        except (ValueError, FileNotFoundError):
            pass
        shutil.rmtree(dest)
    elif dest.exists() and not marker.exists():
        # Not a shared-skill copy — possibly a personal skill that was
        # placed here outside the normal sync path. Leave it alone.
        return

    shutil.copytree(src, dest)
    marker.write_text(str(src_mtime))
    logger.debug("Copied skill: %s -> %s", src.name, dest)


def _sync_shared_skills(target_dir: Path) -> None:
    """Ensure every shared skill is present in *target_dir*, and remove
    entries for skills that no longer exist in the shared-skills store.

    *Unix* — uses symlinks (instant).
    *Windows* — copies directories, but skips when the source mtime hasn't
    changed since the last copy.
    """
    shared_src = DATA_ROOT / "shared-skills"
    target_dir.mkdir(parents=True, exist_ok=True)

    # Collect expected names
    expected: set[str] = set()
    if shared_src.exists():
        for d in shared_src.iterdir():
            if d.is_dir():
                expected.add(d.name)

    _cleanup_stale_skill_entries(target_dir, expected)

    if not shared_src.exists():
        return

    is_windows = platform.system() == "Windows"
    for skill_dir in sorted(shared_src.iterdir()):
        if not skill_dir.is_dir():
            continue
        dest = target_dir / skill_dir.name

        # Skip personal skills — they are real directories uploaded by
        # the user, not shared-skill copies / symlinks we manage.
        if dest.exists() and not dest.is_symlink():
            if is_windows:
                marker = dest / ".shared_skill_source"
                if not marker.exists():
                    continue  # personal skill, leave alone
            else:
                continue  # not a symlink → personal skill, leave alone

        if is_windows:
            _sync_skill_copy(skill_dir, dest)
        else:
            _sync_skill_symlink(skill_dir, dest)


# ── SDK option builders ──────────────────────────────────────────────


def build_sdk_options(
    user_id: str,
    can_use_tool_callback=None,
    resume_session_id: str | None = None,
) -> ClaudeAgentOptions:
    """Build ClaudeAgentOptions with full configuration."""
    mcp_config = load_mcp_config_sync()
    skills = load_skills(user_id)
    max_turns = int(os.getenv("MAX_TURNS", "200"))

    # Build MCP servers dict in SDK format (only enabled servers)
    mcp_servers: dict[str, Any] = {}
    for server_name, cfg in mcp_config.get("mcpServers", {}).items():
        if not cfg.get("enabled", True):
            continue
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

    # Sync shared skills into the workspace so the Claude CLI can discover
    # them (the CLI auto-discovers skills from <cwd>/.claude/skills).
    # Personal skills are already stored here by the upload endpoint.
    user_dir = user_data_dir(user_id)
    workspace = user_dir / "workspace"
    project_skills = workspace / ".claude" / "skills"
    _sync_shared_skills(project_skills)

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

    # Build custom env dict for SDK CLI subprocess
    # IMPORTANT: The claude CLI uses ANTHROPIC_AUTH_TOKEN (not ANTHROPIC_API_KEY)
    # when a custom ANTHROPIC_BASE_URL is set. ANTHROPIC_API_KEY only works with
    # the default api.anthropic.com endpoint.
    sdk_env: dict[str, str] = {}
    api_key = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    model = os.getenv("MODEL", "claude-sonnet-4-6")
    if api_key:
        sdk_env["ANTHROPIC_AUTH_TOKEN"] = api_key
    if base_url:
        sdk_env["ANTHROPIC_BASE_URL"] = base_url
    if model:
        sdk_env["MODEL"] = model

    options = ClaudeAgentOptions(
        model=model,
        cwd=str(user_dir / "workspace"),
        system_prompt=build_system_prompt(user_id, skills, workspace),
        allowed_tools=build_allowed_tools(mcp_config),
        max_turns=max_turns,
        permission_mode="acceptEdits",
        mcp_servers=mcp_servers if mcp_servers else None,
        can_use_tool=can_use_tool_callback,
        hooks=hooks,
        include_partial_messages=True,  # Enable streaming text output
        env=sdk_env if sdk_env else None,  # Pass env vars to CLI subprocess
        resume=resume_session_id,  # Resume a previous CLI session (native multi-turn)
        max_buffer_size=int(os.getenv("MAX_BUFFER_SIZE", str(10 * 1024 * 1024))),
    )
    logger.info("[AGENT_CONFIG] key=%s, base_url=%s, model=%s",
                ("SET (via %s)" % ("ANTHROPIC_AUTH_TOKEN" if os.getenv("ANTHROPIC_AUTH_TOKEN") else "ANTHROPIC_API_KEY")) if api_key else "NOT_SET",
                base_url or "default",
                model)
    return options


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
            text = " ".join(b.text for b in content if isinstance(b, TextBlock))
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
        result = {
            "type": "stream_event",
            "uuid": msg.uuid,
            "event": msg.event,
            "session_id": msg.session_id,
        }
        # Extract index from event if present (for content_block_delta)
        if msg.event and "index" in msg.event:
            result["index"] = msg.event["index"]
        yield result
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
        buffer.add_message(
            session_id,
            {
                "type": "tool_use",
                "name": "AskUserQuestion",
                "id": f"ask_{uuid.uuid4().hex[:8]}",
                "input": tool_input,
            },
        )

        # Wait for user answer via WebSocket
        answer_future: asyncio.Future = asyncio.get_event_loop().create_future()
        pending_answers[session_id] = answer_future
        try:
            answer = await asyncio.wait_for(answer_future, timeout=300)
            # Return the answer as the tool result — the bundled CLI expects
            # the key "answers" (plural) to match the AskUserQuestion protocol.
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
            pending_answers.pop(session_id, None)

    # All other tools: allow
    return PermissionResultAllow(behavior="allow")


MAX_CONTINUATION_WINDOW = int(os.getenv("MAX_CONTINUATION_WINDOW", "20"))
# Doubled from 10→20 to provide richer context for multi-turn debugging
# and tool-use chains without agent losing track of earlier steps.
MAX_PROMPT_LENGTH = int(os.getenv("MAX_PROMPT_LENGTH", "8000"))


def _build_history_prompt(history: list[dict[str, Any]], user_message: str) -> str:
    """Build a multi-turn conversation prompt from history + new message.

    Controls:
    - Window: only the last N messages are included (configurable, default 20)
    - Truncation: tool_result content capped at 200 chars; tool_use records name only
    - Length: total prompt capped at MAX_PROMPT_LENGTH chars; oldest messages dropped first
    - System messages and empty content are skipped
    - The final user message is always preserved
    - Assistant messages are not explicitly excluded — they pass through as part of
      history so the model can reference its own prior responses (important for
      multi-turn context with Echo agents).
    """
    parts: list[str] = []

    # Step 1: format user messages and tool records
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
    user_id: str,
    session_id: str,
    user_message: str,
    is_continuation: bool = False,
    attached_files: list[str] | None = None,
) -> None:
    """Run the agent using ClaudeSDKClient for bidirectional interaction.

    When *is_continuation* is True, historical messages are replayed to the
    Claude CLI so the agent has full conversation context.
    When *attached_files* is provided, file names are mentioned in the prompt
    so the agent knows which files the user uploaded.
    """
    from src.agent_logger import AgentLogger

    logger.info("[AGENT_TASK] Starting task: session=%s, user=%s, continuation=%s, message=%s",
                session_id, user_id, is_continuation, user_message[:50])

    agent_log = AgentLogger(user_id=user_id)
    agent_log.start_session(session_id, user_message=user_message)
    start_time = time.time()

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
        result = await _can_use_tool_for_session(session_id, tool_name, tool_input, ctx)
        agent_log.tool_result(tool_name, str(result), session_id=session_id)
        return result

    # Snapshot workspace files before the agent task — used to detect
    # files created by Bash commands after the task ends.
    # Scan outputs/ recursively (to catch files in subdirectories) and
    # workspace root (non-recursive) — full rglob over the entire
    # workspace is too expensive for large projects.
    workspace_snapshot: dict[str, float] = {}
    outputs_dir = workspace / "outputs"
    if outputs_dir.exists():
        for f in outputs_dir.rglob("*"):
            if f.is_file():
                workspace_snapshot[f.relative_to(workspace).as_posix()] = f.stat().st_mtime
    if workspace.exists():
        for f in workspace.iterdir():
            if f.is_file():
                workspace_snapshot[f.relative_to(workspace).as_posix()] = f.stat().st_mtime

    # Look up CLI-generated session UUID for native SDK resume on continuation turns
    cli_uuid = _get_cli_session_uuid(session_id) if is_continuation else None
    options = build_sdk_options(
        user_id,
        can_use_tool_callback=can_use_tool_cb,
        resume_session_id=cli_uuid,
    )

    # Each turn creates a fresh client — CLI subprocess terminates after
    # receive_response() completes, so cached clients are invalid.
    client = ClaudeSDKClient(options)

    try:
        if is_continuation:
            if cli_uuid:
                # Native SDK resume — CLI loads full session history automatically.
                # No manual _build_history_prompt needed; the CLI maintains context.
                try:
                    logger.info(
                        "[AGENT_TASK] Continuation %s: using native resume (CLI UUID=%s)",
                        session_id,
                        cli_uuid,
                    )
                    await client.connect()
                    await client.query(user_message)
                    logger.info("[AGENT_TASK] Resume connected, starting receive_response")
                except Exception as resume_err:
                    logger.warning(
                        "[AGENT_TASK] Native resume failed for %s: %s — falling back to manual history",
                        session_id,
                        resume_err,
                    )
                    # Disconnect the failed client before replacing it
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    # Create a fresh client without resume
                    fallback_options = build_sdk_options(
                        user_id,
                        can_use_tool_callback=can_use_tool_cb,
                        resume_session_id=None,
                    )
                    client = ClaudeSDKClient(fallback_options)
                    cli_uuid = None  # trigger fallback path below
            if not cli_uuid:
                # Fallback: no CLI UUID yet (or resume failed) — reconstruct
                # conversation as a multi-turn prompt.
                # can_use_tool requires streaming mode — wrap prompt as AsyncIterable.
                history = buffer.get_history(session_id, after_index=0)
                full_prompt = _build_history_prompt(history, user_message)

                async def prompt_stream():
                    yield {
                        "type": "user",
                        "message": {"role": "user", "content": full_prompt},
                        "parent_tool_use_id": None,
                        "session_id": "default",
                    }

                logger.info(
                    "[AGENT_TASK] Continuation %s (fallback): prompt length=%d chars, window=%d",
                    session_id,
                    len(full_prompt),
                    len(history),
                )
                await client.connect(prompt=prompt_stream())
                logger.info("[AGENT_TASK] Client connected (continuation fallback), starting receive_response")
        else:
            # First message — connect normally, then query
            logger.info("[AGENT_TASK] Connecting client (first message)")
            await client.connect()
            # Include file attachment info so the agent knows what files were uploaded
            prompt = _format_first_message_prompt(user_message, attached_files)
            logger.info("[AGENT_TASK] Sending query: prompt=%s", prompt[:100])
            await client.query(prompt)
            logger.info("[AGENT_TASK] Query sent, starting receive_response")

        # Receive messages until result
        msg_count = 0
        generated_files: list[dict[str, Any]] = []
        buffered_result: dict[str, Any] | None = None  # SDK result for reordering
        logger.debug("[AGENT_TASK] Starting receive_response loop")
        async for msg in client.receive_response():
            msg_count += 1
            logger.debug("[AGENT_TASK] Received message #%d: type=%s", msg_count, type(msg).__name__)
            for event in message_to_dicts(msg):
                # User message already persisted at function start — skip duplicates from agent response
                if event.get("type") == "user":
                    continue
                # Buffer the SDK result message so file_result can be emitted
                # first, ensuring file cards appear before "Session completed".
                if event.get("type") == "result":
                    buffered_result = event
                    continue
                # AskUserQuestion is already buffered by _can_use_tool_for_session
                if event.get("type") == "tool_use" and event.get("name") == "AskUserQuestion":
                    continue
                # Track Write tool use to collect generated files
                if event.get("type") == "tool_use" and event.get("name") == "Write":
                    tool_input = event.get("input") or {}
                    file_path = tool_input.get("file_path", "")
                    if file_path and should_include_generated_file(Path(file_path).name):
                        # Skip skill-related files — they live in .claude/skills/ or
                        # shared-skills/, not in outputs/, so download URLs would 404.
                        if ".claude/skills/" in file_path or "shared-skills/" in file_path:
                            continue
                        # Preserve subdirectory path (e.g. outputs/reports/report.docx)
                        # so the filename field matches the disk scan format.
                        if Path(file_path).is_absolute():
                            try:
                                filename = Path(file_path).relative_to(workspace).as_posix()
                            except ValueError:
                                filename = Path(file_path).name
                        else:
                            filename = file_path.replace("\\", "/")
                        content = tool_input.get("content", "")
                        try:
                            size = len(content.encode("utf-8"))
                        except Exception:
                            size = len(content)
                        generated_files.append(
                            {
                                "filename": filename,
                                "size": size,
                                "generated_at": datetime.now(timezone.utc).isoformat(),
                                "download_url": build_download_url(user_id, file_path, directory="outputs"),
                            }
                        )
                # Truncate oversized tool results
                if event.get("type") == "tool_result":
                    from src.truncation import truncate_tool_output

                    content = event.get("content", "")
                    if content and len(content) > 1000:
                        event["content"] = truncate_tool_output(content)
                buffer.add_message(session_id, event)

        # Persist CLI-generated session UUID as soon as the receive_response
        # loop succeeds, before files-scan code that could throw.
        if buffered_result and buffered_result.get("session_id"):
            _store_cli_session(session_id, buffered_result["session_id"])

        # Detect files created/modified by Bash commands since the task started
        seen_filenames: set[str] = {f["filename"] for f in generated_files}
        outputs_dir = workspace / "outputs"

        # 1. Scan outputs/ recursively for new/modified files (primary generated file location).
        #    Subdirectory files (e.g. outputs/reports/result.docx) are now detected and
        #    their relative path is preserved in the filename field for correct download URLs.
        if outputs_dir.exists():
            for f in outputs_dir.rglob("*"):
                if not f.is_file():
                    continue
                if not should_include_generated_file(f.name):
                    continue
                rel = f.relative_to(workspace).as_posix()
                mtime = f.stat().st_mtime
                if rel not in workspace_snapshot or mtime > workspace_snapshot[rel]:
                    if rel not in seen_filenames:
                        seen_filenames.add(rel)
                        generated_files.append(
                            {
                                "filename": rel,
                                "size": f.stat().st_size,
                                "generated_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                                "download_url": f"/api/users/{user_id}/download/{rel}",
                            }
                        )

        # 2. Scan workspace root for data files that should be in outputs/
        #    Only downloadable result files are included; scripts and logs are left in place
        #    and not shown to the user as generated files.
        if workspace.exists():
            for f in workspace.iterdir():
                if not f.is_file() or f.name.startswith("."):
                    continue
                rel = f.relative_to(workspace).as_posix()
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
                            generated_files.append(
                                {
                                    "filename": f.name,
                                    "size": dest.stat().st_size,
                                    "generated_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                                    "download_url": f"/api/users/{user_id}/download/outputs/{f.name}",
                                }
                            )
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
                            generated_files.append(
                                {
                                    "filename": f.name,
                                    "size": dest.stat().st_size,
                                    "generated_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                                    "download_url": f"/api/users/{user_id}/download/outputs/{f.name}",
                                }
                            )
                        except Exception as e:
                            logger.warning("Failed to relocate stray file %s: %s", f, e)

        # Emit file_result message if the agent generated any files this turn.
        # Uses add_message() (append order) — file_result is emitted BEFORE
        # session_state_changed:completed, so it appears before "Session completed"
        # in both live streaming and DB replay.
        # Filter out infrastructure files (logs, caches, etc.) and invalid filenames
        generated_files = [
            f for f in generated_files if f.get("filename") and should_include_generated_file(f["filename"])
        ]
        if generated_files:
            # Ensure all file entries have download_url
            for f in generated_files:
                if "download_url" not in f:
                    f["download_url"] = build_download_url(user_id, f["filename"], directory="outputs")
            buffer.add_message(
                session_id,
                {
                    "type": "file_result",
                    "content": "",
                    "session_id": session_id,
                    "user_id": user_id,
                    "data": generated_files,
                },
            )

        logger.info(
            "Agent task %s: completed with %d messages in %.1fs",
            session_id,
            msg_count,
            time.time() - start_time,
        )
        # Add state change BEFORE mark_done() so the subscribe loop's
        # final pull (after is_done() returns True) catches the message.
        buffer.add_message(
            session_id,
            {
                "type": "system",
                "subtype": "session_state_changed",
                "state": "completed",
            },
        )
        # Re-add the buffered SDK result AFTER file_result and state_change
        # so "Session completed" appears as the last visible message.
        if buffered_result is not None:
            buffer.add_message(session_id, buffered_result)
        buffer.mark_done(session_id)
        duration_ms = (time.time() - start_time) * 1000
        agent_log.end_session(session_id, status="completed")

        # Auto-generate session title from first user message (backend-driven,
        # not dependent on frontend WebSocket events).
        if session_store is not None:
            try:
                sessions = await session_store.list_sessions(user_id)
                session_info = next((s for s in sessions if s["session_id"] == session_id), None)
                if session_info and not session_info.get("title"):
                    history = buffer.get_history(session_id)
                    first_user = next((m for m in history if m.get("type") == "user"), None)
                    if first_user:
                        title = (first_user.get("content") or "")[:50].strip()
                        if title:
                            await session_store.update_session_title(user_id, session_id, title)
                            logger.info("[AGENT_TASK] Auto-title: %s", title[:40])
            except Exception:
                logger.warning("[AGENT_TASK] Auto-title failed", exc_info=True)

    except asyncio.TimeoutError:
        buffer.add_message(
            session_id,
            {
                "type": "system",
                "subtype": "session_timeout",
                "message": "Agent task timed out. The agent may be stuck processing a file.",
            },
        )
        buffer.add_message(
            session_id,
            {
                "type": "system",
                "subtype": "session_state_changed",
                "state": "error",
            },
        )
        buffer.mark_done(session_id)
        agent_log.end_session(session_id, status="timeout")
    except asyncio.CancelledError:
        buffer.add_message(
            session_id,
            {
                "type": "system",
                "subtype": "session_cancelled",
                "message": "Session cancelled by user.",
            },
        )
        # Add state change BEFORE mark_done() for the same reason.
        buffer.add_message(
            session_id,
            {
                "type": "system",
                "subtype": "session_state_changed",
                "state": "cancelled",
            },
        )
        buffer.mark_done(session_id)
        agent_log.end_session(session_id, status="cancelled")
    except Exception as e:
        error_msg = str(e)
        # Detect SDK JSON buffer overflow and provide a clear message
        if "JSON message exceeded maximum buffer size" in error_msg:
            logger.warning(
                "Agent task %s: SDK JSON buffer overflow — tool output too large, truncated",
                session_id,
            )
            error_msg = (
                "A tool produced too much output and was truncated to avoid "
                "overwhelming the system. Try narrowing your request or "
                "processing the data in smaller steps."
            )
        else:
            logger.exception("Agent task failed for session %s", session_id)
        buffer.add_message(
            session_id,
            {
                "type": "error",
                "message": error_msg,
            },
        )
        # Add state change BEFORE mark_done() so the error is delivered.
        buffer.add_message(
            session_id,
            {
                "type": "system",
                "subtype": "session_state_changed",
                "state": "error",
            },
        )
        buffer.mark_done(session_id)
        agent_log.end_session(session_id, status="error")
    # Note: do NOT disconnect — client is kept alive for follow-ups


# ── WebSocket endpoint ───────────────────────────────────────────


async def _safe_ws_send(websocket: WebSocket, data: dict) -> bool:
    """Send a JSON message over WebSocket, returning False if the connection
    is already closed. Prevents RuntimeError from crashing the subscribe loop."""
    try:
        await websocket.send_text(json.dumps(data))
        return True
    except RuntimeError:
        # WebSocket was already closed — connection lost.
        # Caller should exit the subscribe loop gracefully.
        return False
    except asyncio.CancelledError:
        raise  # Allow task cancellation to propagate
    except Exception:
        # Catch any other send errors (e.g., ConnectionClosed from websockets lib)
        return False


@app.websocket("/ws")
async def handle_ws(websocket: WebSocket) -> None:
    """Browser ↔ Agent WebSocket. Direct SDK integration (Phase 1)."""
    from src.auth import ENFORCE_AUTH, require_user_match, verify_token

    token = websocket.query_params.get("token")
    _verified_user_id: str | None = None
    if ENFORCE_AUTH and token:
        try:
            _verified_user_id = verify_token(token)
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
                if ENFORCE_AUTH and _verified_user_id:
                    user_id = _verified_user_id
                else:
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
            logger.info("[WS] Outer loop: draining queue...")
            while True:
                try:
                    item = pending_ws_msgs.get_nowait()
                    if item is None:
                        return  # WebSocket closed
                    logger.info("[WS] Drained item: type=%s session_id=%s", item.get("type"), item.get("session_id"))
                    if item.get("type") == "answer":
                        sid = item.get("session_id", "")
                        answers = item.get("answers", {})
                        future = pending_answers.get(sid)
                        if future and not future.done():
                            future.set_result(answers)
                    elif item.get("type") == "recover":
                        # Route recover messages to the main handler
                        data = item
                        break
                    else:
                        data = item
                        break
                except asyncio.QueueEmpty:
                    logger.info("[WS] Queue empty after drain")
                    break

            # If no queued message, wait for the next one
            if data is None:
                logger.info("[WS] Outer loop: waiting for message...")
                item = await pending_ws_msgs.get()
                if item is None:
                    logger.info("[WS] Received None (WebSocket closed)")
                    return  # WebSocket closed
                logger.info("[WS] Received item: type=%s session_id=%s", item.get("type"), item.get("session_id"))
                if item.get("type") == "answer":
                    sid = item.get("session_id", "")
                    answers = item.get("answers", {})
                    future = pending_answers.get(sid)
                    if future and not future.done():
                        future.set_result(answers)
                    continue
                elif item.get("type") == "recover":
                    data = item
                else:
                    data = item

            logger.info("[WS] Processing message: type=%s session_id=%s message=%s", data.get("type"), data.get("session_id"), data.get("message", "")[:50])
            user_message = data.get("message", "")
            session_id = data.get("session_id")
            last_index = data.get("last_index", 0)
            attached_files = data.get("files") or None
            client_msg_id = data.get("client_msg_id")  # Frontend UUID for dedup

            if not session_id:
                session_id = f"session_{user_id}_{time.time()}_{uuid.uuid4().hex[:8]}"

            # If we're subscribed to a different session, unsubscribe first
            if current_session_id and current_session_id != session_id:
                current_session_id = None

            # Send historical messages (reconnection recovery)
            history = buffer.get_history(session_id, after_index=last_index)
            for i, h in enumerate(history):
                if not await _safe_ws_send(websocket, {
                    **h,
                    "index": last_index + i,
                    "replay": True,
                    "session_id": session_id,
                }):
                    break

            # ── Recover: read-only replay + subscribe (no agent task) ────────
            if data.get("type") == "recover":
                logger.info("[WS] Entering recover loop for session=%s", session_id)
                current_session_id = session_id
                last_seen = last_index + len(history)
                event = buffer.subscribe(session_id)

                try:
                    while True:
                        # Check for new WebSocket messages
                        try:
                            item = pending_ws_msgs.get_nowait()
                            if item is None:
                                return  # WebSocket closed
                            logger.info("[WS] Recover loop got item: type=%s session_id=%s", item.get("type"), item.get("session_id"))
                            # Always process answers regardless of session — they're time-sensitive
                            if item.get("type") == "answer":
                                sid = item.get("session_id", "")
                                answers = item.get("answers", {})
                                future = pending_answers.get(sid)
                                if future and not future.done():
                                    future.set_result(answers)
                            elif item.get("session_id") and item.get("session_id") != session_id:
                                # Different session — re-queue for the outer loop to handle
                                logger.info("[WS] Recover loop: different session, re-queuing")
                                pending_ws_msgs.put_nowait(item)
                                break
                            elif item.get("type") == "recover":
                                continue  # ignore duplicate recover for SAME session
                            else:
                                # New chat message for this session — break out
                                # so the outer loop can create the agent task
                                logger.info("[WS] Recover loop: new chat for same session, re-queuing and breaking")
                                pending_ws_msgs.put_nowait(item)
                                break
                        except asyncio.QueueEmpty:
                            pass

                        # Pull new messages
                        new_messages = buffer.get_history(session_id, after_index=last_seen)
                        sent_count = 0
                        for i, h in enumerate(new_messages):
                            idx = last_seen + i
                            if not await _safe_ws_send(websocket, {
                                **h,
                                "index": idx,
                                "replay": False,
                                "session_id": session_id,
                            }):
                                break
                            sent_count += 1
                        last_seen += sent_count

                        # If session is done, final pull and exit
                        if buffer.is_done(session_id):
                            final_messages = buffer.get_history(session_id, after_index=last_seen)
                            final_sent = 0
                            for i, h in enumerate(final_messages):
                                idx = last_seen + i
                                if not await _safe_ws_send(websocket, {
                                    **h,
                                    "index": idx,
                                    "replay": False,
                                    "session_id": session_id,
                                }):
                                    break
                                final_sent += 1
                            last_seen += final_sent

                        last_seen, ok = await _emit_synthetic_state_change_if_missing(
                            websocket, session_id, last_seen
                        )
                        if not ok:
                            break
                        break

                    ws_msg = await _wait_for_ws_or_buffer(event, pending_ws_msgs, HEARTBEAT_INTERVAL)
                    if ws_msg:
                        continue  # new WS message — re-check at top
                    # Timeout → send heartbeat
                    task_key = f"task_{session_id}"
                    agent_alive = (
                        (task_key in active_tasks and not active_tasks[task_key].done())
                        or buffer.get_state(session_id) == "running"
                    )
                    hb = make_heartbeat(agent_alive=agent_alive)
                    if not await _safe_ws_send(websocket, {
                        **hb,
                        "index": last_seen,
                        "replay": False,
                        "session_id": session_id,
                    }):
                        break
                    continue
                finally:
                    buffer.unsubscribe(session_id, event)
                    current_session_id = None

                continue  # Back to outer loop

            # ── Chat: start or reuse agent task ──────────────────────────────
            task_key = f"task_{session_id}"
            if task_key not in _task_locks:
                _task_locks[task_key] = asyncio.Lock()

            async with _task_locks[task_key]:
                task_is_new = task_key not in active_tasks or active_tasks[task_key].done()
                if task_is_new:
                    # Check if this is a continuation (has prior history)
                    buf_state = buffer.sessions.get(session_id)
                    has_history = buf_state and len(buf_state.get("messages", [])) > 0
                    is_continuation = has_history

                    if buf_state:
                        buf_state["done"] = False
                        buf_state["state"] = "running"

                    # Buffer user message BEFORE agent task starts — ensures recovery
                    # includes the user message even during the race window before
                    # run_agent_task reaches its add_message call.
                    user_msg_buf: dict[str, Any] = {"type": "user", "content": user_message}
                    if attached_files:
                        user_msg_buf["data"] = [{"filename": f} for f in attached_files]
                    if client_msg_id:
                        user_msg_buf["client_msg_id"] = client_msg_id
                    buffer.add_message(session_id, user_msg_buf)

                    # Broadcast running state to frontend via WebSocket
                    buffer.add_message(
                        session_id,
                        {
                            "type": "system",
                            "subtype": "session_state_changed",
                            "state": "running",
                        },
                    )

                    task = asyncio.create_task(
                        asyncio.wait_for(
                            run_agent_task(
                                user_id,
                                session_id,
                                user_message,
                                is_continuation=is_continuation,
                                attached_files=attached_files,
                            ),
                            timeout=float(os.getenv("AGENT_TASK_TIMEOUT", "300")),
                        )
                    )
                    active_tasks[task_key] = task

                    # Add done callback to log task completion/failure
                    def task_done_callback(t):
                        try:
                            exc = t.exception()
                            if exc:
                                logger.error("[AGENT_TASK] Task %s failed: %s", task_key, exc)
                            else:
                                logger.info("[AGENT_TASK] Task %s completed successfully", task_key)
                        except asyncio.CancelledError:
                            logger.info("[AGENT_TASK] Task %s was cancelled", task_key)
                        except Exception as e:
                            logger.error("[AGENT_TASK] Task %s callback error: %s", task_key, e)
                    task.add_done_callback(task_done_callback)

                    logger.info(
                        "WS: created new task for session %s (continuation=%s)",
                        session_id,
                        is_continuation,
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
                        # Always process answers regardless of session — they're time-sensitive
                        if item.get("type") == "answer":
                            sid = item.get("session_id", "")
                            answers = item.get("answers", {})
                            future = pending_answers.get(sid)
                            if future and not future.done():
                                future.set_result(answers)
                        elif item.get("session_id") and item.get("session_id") != session_id:
                            # Different session — re-queue for the outer loop to handle
                            # after this subscribe loop exits
                            pending_ws_msgs.put_nowait(item)
                            break
                        elif item.get("type") == "recover":
                            continue  # ignore duplicate recover for SAME session
                        else:
                            # Message for same session — process it
                            user_message = item.get("message", "")
                            if user_message:
                                logger.info("WS: new message for active session %s", session_id)
                                # Add the new user message and let the running task handle it
                                buffer.add_message(
                                    session_id,
                                    {
                                        "type": "user",
                                        "content": user_message,
                                        "data": [{"filename": f} for f in item["files"]] if item.get("files") else None,
                                        "client_msg_id": item.get("client_msg_id"),
                                    },
                                )
                    except asyncio.QueueEmpty:
                        pass

                    new_messages = buffer.get_history(session_id, after_index=last_seen)
                    sent_count = 0
                    for i, h in enumerate(new_messages):
                        idx = last_seen + i
                        msg_type = h.get("type", "unknown")
                        msg_subtype = h.get("subtype", "")
                        if msg_type == "system" and msg_subtype == "session_state_changed":
                            logger.debug(
                                "WS: sending state_change=%s for session %s (idx=%d)",
                                h.get("state", "?"),
                                session_id,
                                idx,
                            )
                        if not await _safe_ws_send(websocket, {
                            **h,
                            "index": idx,
                            "replay": False,
                            "session_id": session_id,
                        }):
                            break
                        sent_count += 1
                    last_seen += sent_count

                    # If session is done, pull one final time to ensure
                    # session_state_changed: completed is not missed
                    # (it may have been added after the get_history snapshot).
                    if buffer.is_done(session_id):
                        final_messages = buffer.get_history(session_id, after_index=last_seen)
                        final_sent = 0
                        for i, h in enumerate(final_messages):
                            idx = last_seen + i
                            if not await _safe_ws_send(websocket, {
                                **h,
                                "index": idx,
                                "replay": False,
                                "session_id": session_id,
                            }):
                                break
                            final_sent += 1
                        last_seen += final_sent

                        last_seen, ok = await _emit_synthetic_state_change_if_missing(
                            websocket, session_id, last_seen
                        )
                        if not ok:
                            break
                        break

                    ws_msg = await _wait_for_ws_or_buffer(event, pending_ws_msgs, HEARTBEAT_INTERVAL)
                    if ws_msg:
                        continue  # new WS message — re-check at top
                    # Timeout → send heartbeat
                    task_key = f"task_{session_id}"
                    agent_alive = (
                        (task_key in active_tasks and not active_tasks[task_key].done())
                        or buffer.get_state(session_id) == "running"
                    )
                    hb = make_heartbeat(agent_alive=agent_alive)
                    if not await _safe_ws_send(websocket, {
                        **hb,
                        "index": last_seen,
                        "replay": False,
                        "session_id": session_id,
                    }):
                        # WebSocket closed — exit subscribe loop gracefully
                        break
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
        # Don't try to send error message — the connection is likely already closed
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except asyncio.CancelledError:
            pass
        # Clean up orphaned agent task — if the WS closed while the agent
        # was still running, cancel it to prevent resource leaks.
        task_key = f"task_{current_session_id}" if current_session_id else None
        if task_key and task_key in active_tasks:
            task = active_tasks[task_key]
            if not task.done():
                logger.info(
                    "WS: cancelling orphaned agent task for session %s",
                    current_session_id,
                )
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                # Set error state so the frontend knows the session ended
                buffer.add_message(
                    current_session_id,
                    {
                        "type": "system",
                        "subtype": "session_state_changed",
                        "state": "error",
                    },
                )
                buffer.mark_done(current_session_id)


# ── Helper ───────────────────────────────────────────────────────


async def _wait_for_ws_or_buffer(
    event: asyncio.Event, ws_queue: asyncio.Queue, timeout: float
) -> bool:
    """Wait for buffer activity *or* a new WebSocket message.

    Returns ``True`` when a WS message arrived (already re-queued for
    the caller to process at the top of its loop).  Returns ``False``
    on timeout — the caller should send a heartbeat.
    """
    event.clear()
    queue_get = asyncio.ensure_future(ws_queue.get())
    event_wait = asyncio.ensure_future(event.wait())
    try:
        done, _pending = await asyncio.wait(
            [event_wait, queue_get],
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if queue_get in done:
            ws_queue.put_nowait(queue_get.result())
            return True  # WS message — caller will re-check loop top
        if event_wait in done:
            return True  # buffer data available — re-check at top
        return False  # timeout → heartbeat
    finally:
        for t in (event_wait, queue_get):
            if not t.done():
                t.cancel()


def user_data_dir(user_id: str) -> Path:
    return DATA_ROOT / "users" / user_id


# ── Session Management API ───────────────────────────────────────


@app.post("/api/users/{user_id}/sessions")
async def create_session(user_id: str) -> dict[str, str]:
    """Create a new session for the user."""
    session_id = f"session_{user_id}_{time.time()}_{uuid.uuid4().hex[:8]}"

    # Initialize in MessageBuffer so history/status work immediately
    buffer._ensure_buf(session_id)

    # Persist to DB
    if session_store is not None:
        await session_store.create_session(user_id=user_id, session_id=session_id)

    return {"session_id": session_id, "title": ""}


@app.get("/api/users/{user_id}/sessions", response_model=list[dict[str, Any]])
async def list_sessions(user_id: str) -> list[dict[str, Any]]:
    """List all historical sessions for a user."""
    if session_store is not None:
        return await session_store.list_sessions(user_id=user_id)
    logger.warning("Session store unavailable — returning empty list for list_sessions")
    return []


@app.get("/api/users/{user_id}/sessions/{session_id}/history")
async def get_session_history(user_id: str, session_id: str) -> list[dict[str, Any]]:
    """Get all messages for a historical session.

    Each message includes an absolute 'index' field for consistent
    dedup with WebSocket messages. Index 0 = first message ever
    sent for this session.
    """
    if session_store is not None:
        messages = await session_store.get_session_history(session_id=session_id)
        state = buffer.get_session_state(session_id)
        return [
            {**msg, "index": msg.get("seq", i), "session_id": session_id, "session_state": state.get("state", "idle")}
            for i, msg in enumerate(messages)
        ]
    logger.warning("Session store unavailable — returning empty list for get_session_history")
    return []


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
                        generated.append(
                            {
                                "filename": fname,
                                "size": f.get("size", 0),
                                "generated_at": f.get("generated_at", ""),
                                "download_url": f.get(
                                    "download_url", build_download_url(user_id, fname, directory="outputs")
                                ),
                            }
                        )

    # Also scan the workspace/uploads and workspace/outputs directories recursively
    # for files created during this session (catches files in subdirectories).
    workspace = user_data_dir(user_id) / "workspace"
    for scan_dir_name in ("uploads", "outputs"):
        scan_dir = workspace / scan_dir_name
        if scan_dir.exists():
            for f in scan_dir.rglob("*"):
                if not f.is_file():
                    continue
                rel = f.relative_to(workspace).as_posix()
                if rel in seen or not should_include_generated_file(f.name):
                    continue
                stat = f.stat()
                seen.add(rel)
                generated.append(
                    {
                        "filename": rel,
                        "size": stat.st_size,
                        "generated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                        "download_url": f"/api/users/{user_id}/download/{rel}",
                    }
                )

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
        for f in scan_dir.rglob("*"):
            if not f.is_file():
                continue
            if not should_include_generated_file(f.name):
                continue
            rel = str(f.relative_to(workspace))
            stat = f.stat()
            generated.append(
                {
                    "filename": rel,
                    "size": stat.st_size,
                    "generated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "download_url": f"/api/users/{user_id}/download/{rel}",
                }
            )

    # Sort by generation time descending
    generated.sort(key=lambda x: x.get("generated_at", ""), reverse=True)
    return generated


@app.delete("/api/users/{user_id}/sessions/{session_id}")
async def delete_session(user_id: str, session_id: str) -> dict[str, str]:
    """Delete a session, its messages, in-memory buffer, and active client (free disk)."""
    if session_store is not None:
        await session_store.delete_session(session_id=session_id)

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
    if session_store is not None:
        await session_store.update_session_title(user_id=user_id, session_id=session_id, title=req.title)
    return {"status": "ok", "title": req.title}


@app.post("/api/users/{user_id}/sessions/{session_id}/cancel")
async def cancel_session(user_id: str, session_id: str) -> dict[str, str]:
    """Cancel a running agent task."""
    task_key = f"task_{session_id}"
    task = active_tasks.get(task_key)
    if task and not task.done():
        task.cancel()
        # Wait for the task's CancelledError handler to finish so the
        # buffer state is final and consistent when the client reads it.
        try:
            await task
        except asyncio.CancelledError:
            pass  # Expected — the task was cancelled
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

    # Create new session in DB and copy metadata
    if session_store is not None:
        await session_store.create_session(user_id=user_id, session_id=new_session_id)
        # Copy session title from source
        sessions = await session_store.list_sessions(user_id=user_id)
        src_session = next((s for s in sessions if s["session_id"] == session_id), None)
        if src_session and src_session.get("title"):
            await session_store.update_session_title(
                user_id=user_id, session_id=new_session_id, title=src_session["title"]
            )

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
        buffer_age=state.get("buffer_age", 0.0),
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

    return JSONResponse(
        {
            "status": "ok",
            "filename": filename,
            "size": len(content),
        }
    )


@app.get("/api/users/{user_id}/files")
async def list_files(user_id: str) -> list[dict[str, Any]]:
    """List files in user's workspace."""
    workspace = user_data_dir(user_id) / "workspace"
    files: list[dict[str, Any]] = []
    if workspace.exists():
        for f in workspace.rglob("*"):
            if f.is_file():
                rel = f.relative_to(workspace)
                files.append(
                    {
                        "path": str(rel),
                        "size": f.stat().st_size,
                    }
                )
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
        content = (d / "SKILL.md").read_text()
        frontmatter = parse_skill_frontmatter(content)
        results.append(
            SkillInfo(
                name=d.name,
                source=SkillSource.SHARED,
                content=content,
                description=frontmatter.get("description") or "",
                path=str(d),
                created_at=created_at,
                created_by=created_by,
            )
        )
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
        if not d.is_dir() or d.is_symlink() or (d / ".shared_skill_source").exists():
            continue  # skip symlinks and Windows-copied shared skills
        skill_file = d / "SKILL.md"
        if not skill_file.exists():
            continue
        created_at, created_by = _read_skill_meta(d)
        content = skill_file.read_text()
        frontmatter = parse_skill_frontmatter(content)
        results.append(
            SkillInfo(
                name=d.name,
                source=SkillSource.PERSONAL,
                content=content,
                description=frontmatter.get("description") or "",
                path=str(d),
                created_at=created_at,
                created_by=created_by,
            )
        )
    return results


# ── Skill upload helpers ──────────────────────────────────────────

MAX_ZIP_SIZE = 50 * 1024 * 1024  # 50MB compressed
MAX_UNCOMPRESSED = 100 * 1024 * 1024  # 100MB uncompressed
MAX_SKILL_FILES = 100


def _extract_zip_to_dir(zip_data: bytes, target_dir: Path) -> list[str]:
    """Safely extract a zip file into target_dir. Returns list of extracted paths.

    Automatically strips ALL common leading directory prefixes so that
    SKILL.md ends up at the root of target_dir. E.g.:
    - using-superpowers/using-superpowers/SKILL.md → SKILL.md
    - foo/bar/baz/SKILL.md → SKILL.md (all files share same prefix)
    - mixed/a/SKILL.md + mixed/b/README.md → a/SKILL.md + b/README.md
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

    # Collect all file paths and compute the common leading directory prefix
    file_paths = [e.filename for e in entries if not e.is_dir() and e.filename]
    dirs_per_file = [p.split("/")[:-1] for p in file_paths]
    common_prefix = ""
    if dirs_per_file and all(len(d) > 0 for d in dirs_per_file):
        min_len = min(len(d) for d in dirs_per_file)
        common_parts: list[str] = []
        for i in range(min_len):
            if len(set(d[i] for d in dirs_per_file)) == 1:
                common_parts.append(dirs_per_file[0][i])
            else:
                break
        common_prefix = "/".join(common_parts) + "/" if common_parts else ""

    for entry in entries:
        if entry.is_dir():
            continue
        # Reject symlinks
        file_type = (entry.external_attr >> 16) & 0o170000
        if file_type == 0o120000:
            raise HTTPException(status_code=400, detail="Symlinks not allowed in zip")

        # Strip common leading prefix so SKILL.md lands at target root
        rel_path = entry.filename
        if common_prefix and rel_path.startswith(common_prefix):
            rel_path = rel_path[len(common_prefix) :]

        if not rel_path:
            continue

        # Path traversal check
        target = (target_dir / rel_path).resolve()
        if not str(target).startswith(str(target_resolved)):
            raise HTTPException(status_code=400, detail=f"Invalid path in zip: {entry.filename}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(entry))
        extracted.append(rel_path)

    return extracted


# ── Skill upload endpoints


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
        meta_path.write_text(
            json.dumps(
                {
                    "source": "upload",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "zip_filename": file.filename,
                },
                indent=2,
            )
        )

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
        meta_path.write_text(
            json.dumps(
                {
                    "source": "upload",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "zip_filename": file.filename,
                },
                indent=2,
            )
        )

    return {"status": "ok", "skill_name": skill_name, "files": extracted}


def _validate_skill_name(skill_name: str, parent_dir: Path) -> None:
    """Reject skill names containing path traversal sequences.

    Raises HTTPException 400 if *skill_name* is empty or contains ``..``,
    ``/``, or ``\\``, or if the resolved path escapes *parent_dir*.
    """
    if not skill_name or not skill_name.strip():
        raise HTTPException(status_code=400, detail="Skill name must not be empty")
    if ".." in skill_name or "/" in skill_name or "\\" in skill_name:
        raise HTTPException(status_code=400, detail="Invalid skill name")
    resolved = (parent_dir / skill_name).resolve()
    if not str(resolved).startswith(str(parent_dir.resolve())):
        raise HTTPException(status_code=400, detail="Invalid skill name")


@app.delete("/api/shared-skills/{skill_name}")
async def delete_shared_skill(skill_name: str) -> dict[str, str]:
    """Delete a shared skill."""
    _validate_skill_name(skill_name, DATA_ROOT / "shared-skills")
    skill_dir = DATA_ROOT / "shared-skills" / skill_name
    if not skill_dir.exists() or not skill_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Shared skill '{skill_name}' not found")
    shutil.rmtree(skill_dir)
    return {"status": "ok"}


@app.delete("/api/users/{user_id}/skills/{skill_name}")
async def delete_skill(user_id: str, skill_name: str) -> dict[str, str]:
    """Delete a personal skill (real directory only, not shared/symlink)."""
    skill_dir = user_data_dir(user_id) / "workspace" / ".claude" / "skills" / skill_name
    _validate_skill_name(skill_name, skill_dir.parent)
    if not skill_dir.exists() or skill_dir.is_symlink():
        raise HTTPException(status_code=404, detail="Personal skill not found")
    shutil.rmtree(skill_dir)
    return {"status": "ok"}


# ── Skill Promotion (personal → shared) ────────────────────────────


@app.post("/api/users/{user_id}/skills/{skill_name}/promote")
async def promote_skill_to_shared(user_id: str, skill_name: str) -> dict[str, Any]:
    """Promote a personal skill directly to shared.

    Copies the skill to ``DATA_ROOT/shared-skills/<skill_name>/``.
    Returns 409 with conflict detail on name collision.
    """
    _validate_skill_name(skill_name, user_data_dir(user_id) / "workspace" / ".claude" / "skills")
    _validate_skill_name(skill_name, DATA_ROOT / "shared-skills")
    personal_dir = user_data_dir(user_id) / "workspace" / ".claude" / "skills" / skill_name
    if not personal_dir.exists() or personal_dir.is_symlink():
        raise HTTPException(status_code=404, detail="Personal skill not found")

    skill_file = personal_dir / "SKILL.md"
    if not skill_file.exists():
        raise HTTPException(status_code=400, detail="Skill directory has no SKILL.md")

    target_dir = DATA_ROOT / "shared-skills" / skill_name

    # Check: same name already exists in shared?
    if target_dir.exists():
        existing_desc = ""
        existing_skill = target_dir / "SKILL.md"
        if existing_skill.exists():
            fm = parse_skill_frontmatter(existing_skill.read_text())
            existing_desc = fm.get("description", "")
        raise HTTPException(
            status_code=409,
            detail=json.dumps({
                "conflict_type": "name_conflict",
                "skill_name": skill_name,
                "existing_description": existing_desc,
                "message": f"A shared skill named '{skill_name}' already exists.",
            }),
        )

    # Apply guardrails: file count and total size (same as upload endpoint)
    _file_count = 0
    _total_size = 0
    for _f in personal_dir.rglob("*"):
        if _f.is_file():
            _file_count += 1
            _total_size += _f.stat().st_size
    if _file_count > MAX_SKILL_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Skill has {_file_count} files; max is {MAX_SKILL_FILES}",
        )
    if _total_size > MAX_UNCOMPRESSED:
        raise HTTPException(
            status_code=400,
            detail=f"Skill size ({_total_size} bytes) exceeds max ({MAX_UNCOMPRESSED} bytes)",
        )

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(personal_dir, target_dir)

    # Write promotion metadata to skill-meta.json
    meta_path = target_dir / "skill-meta.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    meta["promoted_by"] = user_id
    meta["promoted_at"] = datetime.now(timezone.utc).isoformat()
    meta["source"] = meta.get("source", "promoted")
    meta_path.write_text(json.dumps(meta, indent=2))

    return {
        "status": "ok",
        "skill_name": skill_name,
        "message": f"Skill '{skill_name}' promoted to shared.",
    }


# ── Memory API ───────────────────────────────────────────────────


@app.get("/api/users/{user_id}/memory")
async def get_memory(user_id: str) -> dict[str, Any]:
    """Get user's platform memory (L1). Uses DB with file fallback."""
    from src.memory import MemoryManager

    mgr = MemoryManager(user_id=user_id, data_root=DATA_ROOT, db=_db)
    return mgr.read()


@app.put("/api/users/{user_id}/memory")
async def update_memory(user_id: str, update: MemoryUpdate) -> dict[str, str]:
    """Update user's platform memory (deep merge). Uses DB with file fallback."""
    from src.memory import MemoryManager

    mgr = MemoryManager(user_id=user_id, data_root=DATA_ROOT, db=_db)
    patch: dict[str, Any] = {}
    if update.preferences:
        patch["preferences"] = update.preferences
    if update.entity_memory:
        patch["entity_memory"] = update.entity_memory
    if update.audit_context:
        patch["audit_context"] = update.audit_context
    if update.file_memory:
        patch["file_memory"] = update.file_memory
    mgr.update(patch)
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
async def create_task(user_id: str, req: TaskCreateRequest) -> dict[str, str]:
    """Create a new sub-agent task."""
    from src.sub_agent import SubAgentManager

    mgr = SubAgentManager(user_id=user_id, db=_db)
    task_id = await mgr.create_task(
        subject=req.subject,
        description=req.description,
        active_form=req.active_form,
        blocked_by=req.blocked_by,
        parent_task_id=req.parent_task_id,
    )
    return {"task_id": task_id}


@app.get("/api/users/{user_id}/tasks")
async def list_tasks(user_id: str, status: str | None = None) -> list[dict[str, Any]]:
    """List all tasks for the user, optionally filtered by status."""
    from src.sub_agent import SubAgentManager

    return await SubAgentManager(user_id=user_id, db=_db).list_tasks(status=status)


@app.get("/api/users/{user_id}/tasks/{task_id}")
async def get_task(user_id: str, task_id: str) -> JSONResponse:
    """Get a single task by ID."""
    from src.sub_agent import SubAgentManager

    task = await SubAgentManager(user_id=user_id, db=_db).get_task(task_id)
    if task is None:
        return JSONResponse({"error": "task not found"}, status_code=404)
    return JSONResponse(task)


@app.patch("/api/users/{user_id}/tasks/{task_id}")
async def update_task(user_id: str, task_id: str, req: TaskUpdateRequest) -> JSONResponse:
    """Update a task's status or fields."""
    from src.sub_agent import SubAgentManager

    mgr = SubAgentManager(user_id=user_id, db=_db)
    updated = await mgr.update_task(
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

    deleted = await SubAgentManager(user_id=user_id, db=_db).delete_task(task_id)
    if not deleted:
        return {"status": "not_found"}
    return {"status": "ok"}


# ── Skill Feedback ───────────────────────────────────────────────


class SkillFeedbackRequest(BaseModel):
    rating: int
    comment: str = ""
    user_edits: str = ""
    session_id: str | None = None
    skill_version: str | None = None
    conversation_snippet: str = ""


@app.post("/api/skills/{skill_name}/feedback")
async def submit_skill_feedback(
    skill_name: str,
    req: SkillFeedbackRequest,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Submit feedback for a skill."""
    user_id = _get_user_id_from_header(authorization)

    # Use DB-backed manager if database is available
    if _db is not None:
        from src.skill_feedback import DBSkillFeedbackManager

        mgr = DBSkillFeedbackManager(db=_db)
        entry = await mgr.submit_feedback(
            skill_name,
            user_id=user_id,
            rating=req.rating,
            comment=req.comment,
            session_id=req.session_id,
            user_edits=req.user_edits,
            skill_version=req.skill_version or "",
            conversation_snippet=req.conversation_snippet,
        )
    else:
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
    if _db is not None:
        from src.skill_feedback import DBSkillFeedbackManager

        mgr = DBSkillFeedbackManager(db=_db)
        return await mgr.get_analytics(skill_name)

    from src.skill_feedback import SkillFeedbackManager

    mgr = SkillFeedbackManager()
    return mgr.get_analytics(skill_name)


@app.get("/api/admin/skills/analytics")
async def get_all_skills_analytics(
    authorization: str | None = Header(None),
) -> dict[str, dict[str, Any]]:
    """Get analytics for all skills."""
    current_user = _get_user_id_from_header(authorization)
    require_admin(current_user)
    if _db is not None:
        from src.skill_feedback import DBSkillFeedbackManager
        return await DBSkillFeedbackManager(db=_db).get_all_analytics()
    from src.skill_feedback import SkillFeedbackManager
    return SkillFeedbackManager().get_all_analytics()


@app.get("/api/skills/{skill_name}/suggestions")
async def get_skill_suggestions(skill_name: str) -> dict[str, list[str]]:
    """Get improvement suggestions for a skill based on feedback."""
    if _db is not None:
        from src.skill_feedback import DBSkillFeedbackManager
        suggestions = await DBSkillFeedbackManager(db=_db).suggest_improvements(skill_name)
        return {"suggestions": suggestions}
    from src.skill_feedback import SkillFeedbackManager
    return {"suggestions": SkillFeedbackManager().suggest_improvements(skill_name)}


# ── Skill Evolution & A/B Testing ────────────────────────────────


def build_evolution_prompt(
    *,
    skill_name: str,
    skill_path: Path,
    version_dir: Path,
    skill_content: str,
    skill_files: list[str],
    feedback: dict[str, list[dict[str, Any]]],
) -> str:
    """Build a system prompt for the evolution agent session.

    The prompt gives the LLM full context about the current skill and
    user feedback, but does NOT prescribe HOW to improve it. The LLM
    decides autonomously which tools, skills, and files to use.
    """
    high_quality = feedback.get("high_quality", [])
    low_rated = feedback.get("low_rated", [])
    user_edits = feedback.get("user_edits", [])

    feedback_context = ""
    if high_quality:
        feedback_context += "\n### What users liked (rating >= 4):\n"
        for e in high_quality[:10]:
            if e.get("comment"):
                feedback_context += f"- {e['comment']}\n"
    if low_rated:
        feedback_context += "\n### What users found issues with (rating <= 2):\n"
        for e in low_rated[:10]:
            if e.get("comment"):
                feedback_context += f"- {e['comment']}\n"
    if user_edits:
        feedback_context += "\n### What users manually changed:\n"
        for e in user_edits[:10]:
            if e.get("user_edits"):
                feedback_context += f"- {e['user_edits']}\n"

    files_listing = "\n".join(f"  - {f}" for f in skill_files) if skill_files else "  (none)"

    return (
        f"You are improving an existing skill based on user feedback.\n\n"
        f"## Current Skill\n"
        f"Name: {skill_name}\n"
        f"Location: {skill_path}/\n"
        f"Output directory (write all changes here): {version_dir}/\n\n"
        f"## Current SKILL.md\n"
        f"```markdown\n{skill_content}\n```\n\n"
        f"## Current Skill Directory Structure\n"
        f"{files_listing}\n\n"
        f"## User Feedback\n"
        f"{feedback_context}\n\n"
        f"## Your Task\n"
        f"Analyze the current skill and the feedback. Improve the skill to better\n"
        f"address user needs.\n\n"
        f"You have full autonomy in HOW you improve this skill. You may:\n"
        f"- Rewrite SKILL.md entirely\n"
        f"- Add, modify, or delete any files\n"
        f"- Create new scripts/, references/, or assets/ directories\n"
        f"- Delete files that are no longer needed\n"
        f"- Use any available skills (including skill-creator) as reference\n"
        f"- Run scripts or tools to help with your work\n\n"
        f"IMPORTANT: The SKILL.md YAML frontmatter (between --- delimiters)\n"
        f"must be preserved with the same name and description fields.\n\n"
        f"The ONLY requirement is that the final result is a valid skill directory\n"
        f"at the output path. When done, print: EVOLUTION_COMPLETE"
    )


def next_version_number(versions_dir: Path) -> int:
    """Return the next version number based on existing version directories.

    Uses max(existing_versions) + 1 rather than len(existing_versions) + 1
    to avoid collisions when versions are deleted.
    """
    if not versions_dir.exists():
        return 1
    max_ver = 0
    for entry in versions_dir.iterdir():
        if entry.is_dir() and entry.name.startswith("v"):
            try:
                ver = int(entry.name[1:])
                max_ver = max(max_ver, ver)
            except ValueError:
                continue
    return max_ver + 1


class SkillEvolveAgentRequest(BaseModel):
    model: str = "claude-sonnet-4-6"


class SkillActivateRequest(BaseModel):
    version_number: int


@app.post("/api/skills/{skill_name}/activate-version")
async def activate_skill_version(
    skill_name: str,
    req: SkillActivateRequest,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Activate a specific pending version."""
    current_user = _get_user_id_from_header(authorization)
    from src.skill_evolution import SkillEvolutionManager

    mgr = SkillEvolutionManager(db=_db)
    result = await mgr.db_activate_version(skill_name, version_number=req.version_number)
    if result:
        return {
            "status": "ok",
            "activated": True,
            "version_number": result["version_number"],
            "backup": result.get("backup"),
        }
    return {"status": "failed", "reason": f"Version {req.version_number} not found"}


@app.post("/api/skills/{skill_name}/rollback")
async def rollback_skill(
    skill_name: str,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Rollback to the most recent backup version."""
    current_user = _get_user_id_from_header(authorization)
    from src.skill_evolution import SkillEvolutionManager

    mgr = SkillEvolutionManager(db=_db)
    result = await mgr.db_rollback_version(skill_name)
    if result:
        return {
            "status": "ok",
            "rolled_back": True,
            "restored_version": result["restored_version"],
        }
    return {"status": "info", "message": "No backup version found to restore"}


# ── Agent-Driven Skill Evolution ─────────────────────────────────


async def run_evolution_agent(
    skill_name: str,
    user_id: str,
    version_dir: Path,
    task_id: str,
    *,
    model: str = "claude-sonnet-4-6",
) -> dict[str, Any]:
    """Launch an Agent session to evolve a skill based on feedback.

    Unlike the old preview_evolution (which calls `claude --print` for a
    single text rewrite), this starts a full Agent session with access to
    all tools (Read, Write, Edit, Bash, Glob, Grep, WebFetch, WebSearch,
    Agent, Skill) and all available skills (including skill-creator).

    The LLM autonomously decides HOW to improve the skill — whether to
    rewrite SKILL.md, add scripts/references/assets, use skill-creator,
    or any other approach.

    Returns:
        dict with task_id, status, and optionally files/summary on completion.
    """
    from src.skill_evolution import SkillEvolutionManager

    mgr = SkillEvolutionManager(db=_db)

    # Gather feedback data
    feedback = await mgr.db_get_feedback_for_evolution(skill_name)

    # Resolve skill directory
    skill_dir = DATA_ROOT / "shared-skills" / skill_name
    if _db is not None:
        # DB-backed skills live in DATA_ROOT / "shared-skills"
        skill_dir = DATA_ROOT / "shared-skills" / skill_name
    skill_file = skill_dir / "SKILL.md"

    if not skill_file.exists():
        return {"task_id": "", "status": "failed", "reason": "SKILL.md not found"}

    skill_content = skill_file.read_text()

    # List existing skill files
    skill_files = []
    for f in skill_dir.rglob("*"):
        if f.is_file() and f.name != "skill-meta.json":
            skill_files.append(str(f.relative_to(skill_dir)))

    # Create version output directory
    version_dir.mkdir(parents=True, exist_ok=True)

    # Build system prompt
    system_prompt = build_evolution_prompt(
        skill_name=skill_name,
        skill_path=skill_dir,
        version_dir=version_dir,
        skill_content=skill_content,
        skill_files=skill_files,
        feedback=feedback,
    )

    # Build SDK options — reuse the normal session config but with
    # cwd pointing to the version directory
    options = _build_evolution_sdk_options(
        user_id=user_id,
        version_dir=version_dir,
        system_prompt=system_prompt,
        model=model,
    )

    # Create and run the Agent session
    client = ClaudeSDKClient(options)

    try:
        await client.connect()
        await client.query(system_prompt)

        # Collect agent output
        async for msg in client.receive_response():
            # Stream messages to the buffer for real-time monitoring
            msg_dict = _message_to_dict_if_serializable(msg)
            if msg_dict:
                buffer.add_message(task_id, msg_dict)

        # Scan the version directory for generated files
        generated_files = []
        for f in version_dir.rglob("*"):
            if f.is_file():
                generated_files.append(
                    {
                        "path": str(f.relative_to(version_dir)),
                        "size": f.stat().st_size,
                    }
                )

        return {
            "task_id": task_id,
            "status": "complete",
            "files": generated_files,
            "summary": f"Generated {len(generated_files)} files in {version_dir}",
        }
    except Exception as e:
        logger.error("run_evolution_agent failed for %s: %s", skill_name, e)
        buffer.add_message(
            task_id,
            {
                "type": "error",
                "message": str(e),
            },
        )
        return {"task_id": task_id, "status": "failed", "reason": str(e)}


def _build_evolution_sdk_options(
    *,
    user_id: str,
    version_dir: Path,
    system_prompt: str,
    model: str,
) -> "ClaudeAgentOptions":
    """Build ClaudeAgentOptions for an evolution agent session.

    Similar to build_sdk_options but with:
    - cwd pointing to the version output directory
    - custom system prompt with evolution context
    - all normal tools and skills available
    """
    skills = load_skills(user_id)
    max_turns = int(os.getenv("MAX_TURNS", "200"))

    # Sync shared skills into the version directory so the agent can
    # discover and use all available skills (including skill-creator).
    version_skills = version_dir / ".claude" / "skills"
    _sync_shared_skills(version_skills)

    # Copy personal skills (can't symlink across all setups; use mtime
    # caching to skip unchanged skills).
    user_workspace = user_data_dir(user_id) / "workspace"
    user_skills = user_workspace / ".claude" / "skills"
    if user_skills.exists():
        for skill_dir in user_skills.iterdir():
            if skill_dir.is_dir() and not skill_dir.is_symlink():
                dest = version_skills / skill_dir.name
                _sync_skill_copy(skill_dir, dest)

    return ClaudeAgentOptions(
        model=model,
        cwd=str(version_dir),
        system_prompt=system_prompt,
        allowed_tools=build_allowed_tools(load_mcp_config()),
        max_turns=max_turns,
        permission_mode="acceptEdits",
        max_buffer_size=int(os.getenv("MAX_BUFFER_SIZE", str(10 * 1024 * 1024))),
    )


def _message_to_dict_if_serializable(msg: Any) -> dict[str, Any] | None:
    """Convert a Claude SDK message to a dict for broadcasting."""
    try:
        # Use the existing message_to_dicts generator
        dicts = list(message_to_dicts(msg))
        if dicts:
            return dicts[0]  # Return first dict for simplicity
        return None
    except Exception:
        return None


@app.post("/api/skills/{skill_name}/evolve-agent")
async def trigger_skill_evolution_agent(
    skill_name: str,
    req: SkillEvolveAgentRequest,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Launch an Agent session to evolve a skill.

    Unlike /evolve (which does a single LLM text rewrite), this starts
    a full Agent session with all tools and skills available. The LLM
    autonomously decides HOW to improve the skill.
    """
    current_user = _get_user_id_from_header(authorization)

    # Resolve skills directory
    skills_dir = DATA_ROOT / "shared-skills"

    if not (skills_dir / skill_name / "SKILL.md").exists():
        return {"status": "failed", "reason": f"SKILL.md not found for {skill_name}"}

    # Create version output directory
    versions_dir = skills_dir / skill_name / "versions"
    version_num = next_version_number(versions_dir)
    version_dir = versions_dir / f"v{version_num}"

    # Launch the agent session asynchronously
    import threading

    result_holder: dict[str, Any] = {}

    def _run_in_thread() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        task_id = f"evolve-{skill_name}-v{version_num}"
        try:
            result_holder["result"] = loop.run_until_complete(
                run_evolution_agent(
                    skill_name=skill_name,
                    user_id=current_user,
                    version_dir=version_dir,
                    task_id=task_id,
                    model=req.model,
                )
            )
        finally:
            loop.close()

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()

    return {
        "status": "ok",
        "task_id": f"evolve-{skill_name}-v{version_num}",
        "version_number": version_num,
        "version_path": str(version_dir),
        "message": "Agent evolution started. Poll /evolve-status for progress.",
    }


@app.get("/api/skills/{skill_name}/evolve-status/{task_id}")
async def get_evolution_status(
    skill_name: str,
    task_id: str,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Get the status of an evolution task."""
    _get_user_id_from_header(authorization)

    # Check if the task is in the active tasks map
    if task_id in active_tasks:
        return {"status": "running", "task_id": task_id}

    # Check the message buffer for completion
    history = buffer.get_history(task_id, after_index=0)
    is_error = any(m.get("type") == "error" for m in history)
    is_done = any(m.get("type") == "result" or "EVOLUTION_COMPLETE" in str(m.get("content", "")) for m in history)

    if is_error:
        return {"status": "failed", "task_id": task_id, "messages": history[-5:]}
    if is_done:
        # Scan for generated files
        skills_dir = DATA_ROOT / "shared-skills"
        # Extract version from task_id (e.g., "evolve-pdf-editor-v3")
        version_match = re.search(r"v(\d+)$", task_id)
        if version_match:
            version_num = int(version_match.group(1))
            version_dir = skills_dir / skill_name / "versions" / f"v{version_num}"
            if version_dir.exists():
                files = []
                for f in version_dir.rglob("*"):
                    if f.is_file():
                        files.append(
                            {
                                "path": str(f.relative_to(version_dir)),
                                "size": f.stat().st_size,
                            }
                        )
                return {"status": "complete", "task_id": task_id, "files": files}

    return {"status": "running", "task_id": task_id}


@app.get("/api/skills/{skill_name}/version-files/{version_number}")
async def get_version_files(
    skill_name: str,
    version_number: int,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Get the list of files in a specific version."""
    _get_user_id_from_header(authorization)

    skills_dir = DATA_ROOT / "shared-skills"
    version_dir = skills_dir / skill_name / "versions" / f"v{version_number}"

    if not version_dir.exists():
        return {"status": "failed", "reason": f"Version v{version_number} not found"}

    files = []
    for f in version_dir.rglob("*"):
        if f.is_file():
            rel = str(f.relative_to(version_dir))
            files.append(
                {
                    "path": rel,
                    "size": f.stat().st_size,
                    "is_skill_md": rel == "SKILL.md",
                }
            )

    return {"status": "ok", "version": version_number, "files": files}


@app.get("/api/skills/{skill_name}/version-file/{version_number}", response_model=None)
async def get_version_file_content(
    skill_name: str,
    version_number: int,
    file_path: str,
    authorization: str | None = Header(None),
) -> FileResponse | dict[str, Any]:
    """Get content of a specific file in a version."""
    _get_user_id_from_header(authorization)

    skills_dir = DATA_ROOT / "shared-skills"
    version_dir = skills_dir / skill_name / "versions" / f"v{version_number}"
    target = (version_dir / file_path).resolve()

    if not target.exists() or not str(target).startswith(str(version_dir.resolve())):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(str(target))


# ── Legacy: Evolution Candidates (still used by EvolutionPanel) ──
@app.get("/api/admin/skills/evolution-candidates")
async def list_evolution_candidates(
    authorization: str | None = Header(None),
) -> dict[str, list[dict[str, Any]]]:
    """List skills that should evolve."""
    _get_user_id_from_header(authorization)
    from src.skill_evolution import SkillEvolutionManager

    mgr = SkillEvolutionManager(db=_db)
    if _db is not None:
        candidates = await mgr.db_get_evolution_candidates()
    else:
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


@app.get("/api/admin/feedback")
async def list_all_feedback(
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Get all feedback entries across all users."""
    current_user = _get_user_id_from_header(authorization)
    require_admin(current_user)

    if _db is not None:
        from src.skill_feedback import DBSkillFeedbackManager

        mgr = DBSkillFeedbackManager(db=_db)
        items = await mgr.get_all_feedback()

        # Compute stats grouped by skill
        stats_map: dict[str, dict[str, Any]] = {}
        for item in items:
            name = item["skill_name"]
            if name not in stats_map:
                stats_map[name] = {"skill_name": name, "count": 0, "total_rating": 0}
            stats_map[name]["count"] += 1
            stats_map[name]["total_rating"] += item["rating"]

        stats = []
        for s in stats_map.values():
            stats.append(
                {
                    "skill_name": s["skill_name"],
                    "count": s["count"],
                    "avg_rating": round(s["total_rating"] / s["count"], 2),
                }
            )

        return {"stats": stats, "items": items, "total_count": len(items)}

    # Fallback: empty response when no DB available
    return {"stats": [], "items": [], "total_count": 0}


@app.get("/api/skills/{skill_name}/version")
async def get_skill_versions(skill_name: str) -> dict[str, Any]:
    """Get all versions of a skill."""
    from src.skill_evolution import SkillEvolutionManager

    mgr = SkillEvolutionManager(db=_db)
    if _db is not None:
        stats = await mgr.db_get_feedback_stats(skill_name)
    else:
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


@app.get("/api/skills/{skill_name}/version/{version_name}")
async def get_skill_version_content(
    skill_name: str,
    version_name: str,
) -> dict[str, Any]:
    """Get the content of a specific skill version."""
    from src.skill_evolution import SkillEvolutionManager

    mgr = SkillEvolutionManager(db=_db)
    version_file = mgr.skills_dir / skill_name / f"{version_name}.md"
    if not version_file.exists():
        return {"status": "not_found", "reason": f"Version {version_name} not found"}
    content = version_file.read_text()
    return {
        "content": content,
        "name": version_name,
    }


# ── MCP Registry ─────────────────────────────────────────────────


async def _load_mcp_servers() -> list[dict[str, Any]]:
    """Load MCP servers from DB (primary) or file (fallback)."""
    global _mcp_store
    if _mcp_store is not None:
        return await _mcp_store.list_all()
    # File fallback
    config = load_mcp_config_sync()
    return [{"name": name, **cfg} for name, cfg in config.get("mcpServers", {}).items()]


@app.get("/api/admin/mcp-servers")
async def list_mcp_servers(
    authorization: str | None = Header(None),
) -> list[dict[str, Any]]:
    """List all registered MCP servers."""
    user_id = _get_user_id_from_header(authorization)
    require_admin(user_id)
    return await _load_mcp_servers()


@app.post("/api/admin/mcp-servers")
async def register_mcp_server(
    server: McpServerConfig,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Register a new MCP server."""
    global _mcp_store
    user_id = _get_user_id_from_header(authorization)
    require_admin(user_id)
    server_dict = server.model_dump()

    # Auto-discover tools for stdio servers when not explicitly provided
    discover_status = None
    discover_error = None
    if server_dict.get("type") == "stdio" and not server_dict.get("tools"):
        status, _error, tool_names = await _check_stdio_mcp(server_dict)
        if status == "connected" and tool_names:
            server_dict["tools"] = tool_names
            discover_status = status
        elif _error:
            discover_status = status
            discover_error = _error

    if _mcp_store is not None:
        await _mcp_store.create(server_dict)
    else:
        registry = load_mcp_config_sync()
        registry["mcpServers"][server.name] = server_dict
        save_mcp_config(registry)
    return {"status": "ok", "discover_status": discover_status, "discover_error": discover_error}


@app.put("/api/admin/mcp-servers/{server_name}")
async def update_mcp_server(
    server_name: str,
    server: McpServerConfig,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Update an existing MCP server."""
    global _mcp_store
    user_id = _get_user_id_from_header(authorization)
    require_admin(user_id)
    server_dict = server.model_dump()

    # Auto-discover tools for stdio servers when not explicitly provided
    discover_status = None
    discover_error = None
    if server_dict.get("type") == "stdio" and not server_dict.get("tools"):
        status, _error, tool_names = await _check_stdio_mcp(server_dict)
        if status == "connected" and tool_names:
            server_dict["tools"] = tool_names
            discover_status = status
        elif _error:
            discover_status = status
            discover_error = _error

    if _mcp_store is not None:
        result = await _mcp_store.update(server_name, server_dict)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Server '{server_name}' not found")
    else:
        registry = load_mcp_config_sync()
        if server_name not in registry.get("mcpServers", {}):
            raise HTTPException(status_code=404, detail=f"Server '{server_name}' not found")
        registry["mcpServers"][server.name] = server_dict
        if server.name != server_name:
            registry["mcpServers"].pop(server_name, None)
        save_mcp_config(registry)
    return {"status": "ok", "discover_status": discover_status, "discover_error": discover_error}


@app.post("/api/admin/mcp-servers/{server_name}/discover-tools")
async def discover_mcp_tools(
    server_name: str,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Force-refresh the tool list for an MCP server by reconnecting."""
    global _mcp_store
    user_id = _get_user_id_from_header(authorization)
    require_admin(user_id)

    # Load server config
    if _mcp_store is not None:
        server = await _mcp_store.get_by_name(server_name)
    else:
        registry = load_mcp_config_sync()
        server = registry.get("mcpServers", {}).get(server_name)

    if server is None:
        raise HTTPException(status_code=404, detail=f"Server '{server_name}' not found")

    if server.get("type") != "stdio":
        raise HTTPException(status_code=400, detail="Tool discovery only supported for stdio servers")

    status, error, tool_names = await _check_stdio_mcp(server)

    # Update config with discovered tools
    server["tools"] = tool_names
    if _mcp_store is not None:
        await _mcp_store.update(server_name, server)
    else:
        registry = load_mcp_config_sync()
        registry["mcpServers"][server_name] = server
        save_mcp_config(registry)

    return {
        "status": status,
        "error": error,
        "tools": tool_names,
        "tool_count": len(tool_names),
    }


@app.delete("/api/admin/mcp-servers/{server_name}")
async def unregister_mcp_server(
    server_name: str,
    authorization: str | None = Header(None),
) -> dict[str, str]:
    """Unregister an MCP server."""
    global _mcp_store
    user_id = _get_user_id_from_header(authorization)
    require_admin(user_id)
    if _mcp_store is not None:
        await _mcp_store.delete(server_name)
    else:
        registry = load_mcp_config_sync()
        registry["mcpServers"].pop(server_name, None)
        save_mcp_config(registry)
    return {"status": "ok"}


@app.patch("/api/admin/mcp-servers/{server_name}/toggle")
async def toggle_mcp_server(
    server_name: str,
    enabled: bool,
    authorization: str | None = Header(None),
) -> dict[str, str]:
    """Enable/disable an MCP server."""
    global _mcp_store
    user_id = _get_user_id_from_header(authorization)
    require_admin(user_id)
    if _mcp_store is not None:
        await _mcp_store.toggle(server_name, enabled)
    else:
        registry = load_mcp_config_sync()
        if server_name in registry.get("mcpServers", {}):
            registry["mcpServers"][server_name]["enabled"] = enabled
            save_mcp_config(registry)
    return {"status": "ok"}


async def _check_stdio_mcp(cfg: dict[str, Any]) -> tuple[str, str | None, list[str]]:
    """Actually connect to a stdio MCP server and verify it works.

    Returns (status, error_message_or_None, tool_names_list).
    Uses a 30-second timeout to prevent hanging on slow servers
    (e.g. uvx downloading packages on first run).
    """
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=cfg.get("command", ""),
        args=cfg.get("args", []),
        env={k: v for k, v in (cfg.get("env") or {}).items()},
    )

    try:
        async with asyncio.timeout(30):
            async with stdio_client(params, errlog=open(os.devnull, "w")) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools_result = await session.list_tools()
                    tool_names = [t.name for t in tools_result.tools] if tools_result.tools else []
                    return ("connected", None, tool_names)
    except TimeoutError:
        return ("disconnected", "Connection timed out (30s)", [])
    except Exception as e:
        error_msg = str(e)
        if len(error_msg) > 500:
            error_msg = error_msg[:500] + "..."
        return ("disconnected", error_msg, [])


async def _sync_tools_to_db(
    server_name: str,
    discovered_tools: list[str],
    mcp_store: "MCPServerStore",
) -> bool:
    """Persist discovered tools to DB if they differ from stored tools.

    Returns True if tools were updated, False if no change needed.
    """
    existing = await mcp_store.get_by_name(server_name)
    if existing is None:
        return False

    stored_tools = existing.get("tools", [])
    if set(discovered_tools) == set(stored_tools):
        return False

    await mcp_store.update(server_name, {"tools": discovered_tools})
    return True


@app.get("/api/admin/mcp-servers/status")
async def get_mcp_servers_status(
    authorization: str | None = Header(None),
) -> list[dict[str, Any]]:
    """Check the connection status of all MCP servers."""
    user_id = _get_user_id_from_header(authorization)
    require_admin(user_id)

    servers = await _load_mcp_servers()
    results: list[dict[str, Any]] = []

    for cfg in servers:
        server_name = cfg["name"]
        server_type = cfg.get("type", "stdio")
        enabled = cfg.get("enabled", True)

        if not enabled:
            results.append(
                {
                    "name": server_name,
                    "type": server_type,
                    "enabled": False,
                    "status": "disabled",
                    "error": None,
                    "tool_count": 0,
                }
            )
            continue

        if server_type == "stdio":
            command = cfg.get("command", "")
            if not command:
                results.append(
                    {
                        "name": server_name,
                        "type": server_type,
                        "enabled": True,
                        "status": "error",
                        "error": "No command specified",
                        "tool_count": 0,
                    }
                )
            else:
                status, error, tool_names = await _check_stdio_mcp(cfg)
                # Auto-persist discovered tools to DB so agent can use them
                if _mcp_store is not None:
                    await _sync_tools_to_db(server_name, tool_names, _mcp_store)
                results.append(
                    {
                        "name": server_name,
                        "type": server_type,
                        "enabled": True,
                        "status": status,
                        "error": error,
                        "tool_count": len(tool_names),
                    }
                )
        elif server_type == "http":
            url = cfg.get("url", "")
            if not url:
                results.append(
                    {
                        "name": server_name,
                        "type": server_type,
                        "enabled": True,
                        "status": "error",
                        "error": "No URL specified",
                    }
                )
            else:
                try:
                    import httpx

                    async with httpx.AsyncClient() as client:
                        resp = await client.get(url, timeout=3.0)
                        if resp.status_code < 500:
                            results.append(
                                {
                                    "name": server_name,
                                    "type": server_type,
                                    "enabled": True,
                                    "status": "connected",
                                    "error": None,
                                }
                            )
                        else:
                            results.append(
                                {
                                    "name": server_name,
                                    "type": server_type,
                                    "enabled": True,
                                    "status": "disconnected",
                                    "error": f"HTTP {resp.status_code}",
                                }
                            )
                except Exception as e:
                    results.append(
                        {
                            "name": server_name,
                            "type": server_type,
                            "enabled": True,
                            "status": "disconnected",
                            "error": str(e),
                        }
                    )
        else:
            results.append(
                {
                    "name": server_name,
                    "type": server_type,
                    "enabled": enabled,
                    "status": "error",
                    "error": f"Unknown server type: {server_type}",
                }
            )

    return results


def save_mcp_config(config: dict[str, Any]) -> None:
    """File-based fallback — kept for pre-migration compatibility."""
    registry_file = DATA_ROOT / "mcp-registry.json"
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    registry_file.write_text(json.dumps(config, indent=2))


# ── Feedback API ─────────────────────────────────────────────────


@app.post("/api/users/{user_id}/feedback")
async def submit_feedback(user_id: str, feedback: dict[str, Any]) -> dict[str, str]:
    """Collect user feedback for skill evolution."""
    training_dir = DATA_ROOT / "training" / "qa"
    training_dir.mkdir(parents=True, exist_ok=True)

    feedback_file = training_dir / f"{time.time()}_{feedback.get('session_id', 'unknown')}.jsonl"
    with open(feedback_file, "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    **feedback,
                    "user_id": user_id,
                    "timestamp": time.time(),
                },
                ensure_ascii=False,
            )
        )
    return {"status": "ok"}


@app.get("/api/users/{user_id}/feedback")
async def get_user_feedback(user_id: str) -> dict[str, Any]:
    """Get user's feedback records and stats."""
    if _db is not None:
        from src.skill_feedback import DBSkillFeedbackManager

        mgr = DBSkillFeedbackManager(db=_db)
        items = await mgr.get_user_feedback(user_id)
        stats_result = await mgr.get_user_feedback_stats(user_id)
        return {"stats": stats_result["stats"], "items": items, "total_count": stats_result["total_count"]}

    # Fallback: empty response when no DB available
    return {"stats": [], "items": [], "total_count": 0}


# ── Authentication ───────────────────────────────────────────────

from src.auth import create_token, verify_token
from src.admin_auth import require_admin


class TokenRequest(BaseModel):
    user_id: str


def _get_user_id_from_header(authorization: str | None = None) -> str:
    """Extract user_id from Bearer token for admin endpoints."""
    from src.auth import ENFORCE_AUTH

    if not authorization or not authorization.startswith("Bearer "):
        if ENFORCE_AUTH:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authentication token",
            )
        return "default"
    token = authorization.split(" ", 1)[1]
    try:
        return verify_token(token)
    except Exception:
        if not ENFORCE_AUTH:
            return "default"
        raise


@app.post("/api/auth/token")
async def get_auth_token(req: TokenRequest) -> dict[str, str]:
    """Generate a JWT access token for the given user_id."""
    token = create_token(req.user_id)
    return {"token": token, "user_id": req.user_id}


# ── Container Management ──────────────────────────────────────────


@app.get("/api/admin/containers")
async def list_containers(
    authorization: str | None = Header(None),
) -> JSONResponse:
    """List all running user containers."""
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
    authorization: str | None = Header(None),
) -> JSONResponse:
    """Get resource stats for all active containers."""
    user_id = _get_user_id_from_header(authorization)
    require_admin(user_id)
    from src.resource_manager import get_all_resources as _get_all

    return JSONResponse(_get_all())


@app.get("/api/users/{user_id}/resources")
async def get_user_resources(user_id: str) -> JSONResponse:
    """Get resource stats for a specific user's container."""
    from src.resource_manager import check_quota, get_container_stats, get_disk_usage

    return JSONResponse(
        {
            "container": get_container_stats(user_id),
            "disk": get_disk_usage(user_id),
            "quota": check_quota(user_id),
        }
    )


# ── Audit Logs ────────────────────────────────────────────────────


@app.get("/api/admin/audit-logs")
async def query_audit_logs(
    category: str = "auth",
    date: str | None = None,
    user_id: str | None = None,
    action: str | None = None,
    authorization: str | None = Header(None),
) -> list[dict[str, Any]]:
    """Query audit log entries."""
    current_user = _get_user_id_from_header(authorization)
    require_admin(current_user)
    from src.audit_logger import get_audit_logger

    return get_audit_logger().query(
        category,
        date=date,
        user_id=user_id,
        action=action,
    )


# ── Log Cleanup ───────────────────────────────────────────────────


@app.post("/api/admin/logs/cleanup")
async def trigger_log_cleanup(
    authorization: str | None = Header(None),
) -> dict[str, int]:
    """Manually trigger log retention cleanup."""
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
    for subdir in [
        "training/qa",
        "training/skill-feedback",
        "training/preferences",
        "training/skill_outcomes",
        "training/corrections",
    ]:
        (DATA_ROOT / subdir).mkdir(parents=True, exist_ok=True)

    # Initialize SQLite + SessionStore if DATA_DB_PATH is set
    global _db, _mcp_store, buffer, session_store
    db_path_env = os.getenv("DATA_DB_PATH", "")
    if db_path_env:
        db_path = Path(db_path_env)
        if db_path.is_absolute():
            db_path = Path(db_path_env)
        else:
            db_path = Path(__file__).parent / db_path_env
        from src.database import Database
        from src.mcp_store import MCPServerStore, migrate_from_file
        from src.session_store import SessionStore

        _db = Database(db_path=db_path)
        await _db.init()
        buffer.db = _db  # Wire DB into message buffer
        session_store = SessionStore(db=_db)
        logger.info("SQLite initialized: %s (%.2f MB)", db_path, db_path.stat().st_size / (1024 * 1024))

        # Initialize MCP store and migrate from file if needed
        _mcp_store = MCPServerStore(db=_db)
        try:
            registry_file = DATA_ROOT / "mcp-registry.json"
            migrated = await migrate_from_file(registry_file, _mcp_store)
            if migrated > 0:
                logger.info("Migrated %d MCP server entries from file to SQLite", migrated)
                # Backup the original file
                registry_file.rename(registry_file.with_suffix(".json.bak"))
        except Exception:
            logger.exception("MCP migration failed, falling back to file storage")
            _mcp_store = None

        # Migrate any existing JSONL feedback files to SQLite
        try:
            from src.skill_feedback import DBSkillFeedbackManager

            feedback_dir = DATA_ROOT / "training" / "skill-feedback"
            mgr = DBSkillFeedbackManager(db=_db)
            migrated = await mgr.migrate_from_jsonl(feedback_dir)
            if migrated > 0:
                logger.info("Migrated %d JSONL feedback entries to SQLite", migrated)
        except Exception:
            logger.exception("Feedback JSONL migration failed")
    else:
        logger.info("No DATA_DB_PATH set — using file-based storage")

    asyncio.create_task(_cleanup_loop())


async def _cleanup_loop() -> None:
    """Periodically evict stale in-memory session buffers and clean up disk."""
    while True:
        await asyncio.sleep(300)
        buffer.cleanup_expired()
        # Retry unpersisted messages (after transient DB failures)
        flushed = buffer.flush_unpersisted()
        if flushed:
            logger.info("MessageBuffer: flushed %d unpersisted messages to SQLite", flushed)
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

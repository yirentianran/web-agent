"""Shared Pydantic models for the web agent."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ── Session Models ──────────────────────────────────────────────────


class SessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"


class SessionState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"
    WAITING_USER = "waiting_user"
    CANCELLED = "cancelled"


class SessionItem(BaseModel):
    session_id: str
    title: str = ""
    last_message: str = ""
    last_active_at: datetime = Field(default_factory=datetime.utcnow)
    status: SessionStatus = SessionStatus.ACTIVE
    file_count: int = 0


class SessionStatusResponse(BaseModel):
    session_id: str
    state: SessionState = SessionState.IDLE
    cost_usd: float = 0.0
    last_active: float = 0.0
    buffer_age: float = 0.0  # seconds since last buffer activity


# ── Message Models ──────────────────────────────────────────────────


class Message(BaseModel):
    type: str  # assistant | user | system | tool_use | tool_result | error
    content: str = ""
    session_id: str = ""
    index: int = 0
    replay: bool = False
    subtype: Optional[str] = None
    name: Optional[str] = None  # tool name for tool_use/tool_result
    usage: Optional[dict[str, int]] = None  # token usage


# ── Skill Models ────────────────────────────────────────────────────


class SkillSource(str, Enum):
    SHARED = "shared"
    PERSONAL = "personal"


class SkillInfo(BaseModel):
    name: str
    source: SkillSource
    owner: str = ""  # user_id who owns/uploaded the skill
    description: str = ""
    content: str = ""
    path: str = ""
    created_at: str = ""  # ISO 8601 timestamp
    created_by: str = ""  # "upload" | "skill-creator"
    valid: bool = True  # False when SKILL.md is missing or unparseable


class SkillsListResponse(BaseModel):
    skills: list[dict[str, Any]]
    total: int


class SkillUpdateRequest(BaseModel):
    description: str = ""
    category: str = ""
    tags: list[str] = []
    status: str = "active"


class UsageRecord(BaseModel):
    user_id: str = ""
    session_id: str = ""
    version_number: int = 0
    action: str = "use"


# ── MCP Models ──────────────────────────────────────────────────────


class McpServerConfig(BaseModel):
    name: str
    type: str = "stdio"  # stdio | http | sse | streamable_http
    command: Optional[str] = None
    args: list[str] = []
    url: Optional[str] = None
    headers: dict[str, str] = {}
    env: dict[str, str] = {}
    tools: list[str] = []
    resources: list[dict[str, Any]] = []
    prompts: list[dict[str, Any]] = []
    description: str = ""
    enabled: bool = True
    access: str = "all"  # all | admin

    @field_validator("resources", mode="before")
    @classmethod
    def _coerce_resources(cls, v: Any) -> list[dict[str, Any]]:
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        return v

    @field_validator("prompts", mode="before")
    @classmethod
    def _coerce_prompts(cls, v: Any) -> list[dict[str, Any]]:
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        return v

    def model_post_init(self, __context: Any) -> None:
        if self.type == "stdio" and not self.command:
            raise ValueError("command is required for stdio servers")
        if self.type in ("http", "sse", "streamable_http") and not self.url:
            raise ValueError("url is required for HTTP-based transports")


class ToolToggle(BaseModel):
    server: str
    tool: str
    enabled: bool


# ── Feedback Models ─────────────────────────────────────────────────


class SkillFeedback(BaseModel):
    skill_name: str
    session_id: str
    rating: int = Field(ge=1, le=5)
    user_edits: str = ""
    comments: str = ""
    skill_version: str = ""
    timestamp: float = Field(default_factory=lambda: __import__("time").time())


# ── WebSocket Message Types ─────────────────────────────────────────


class WSChatMessage(BaseModel):
    """Client → Server chat message."""

    message: str = ""
    user_id: str
    session_id: Optional[str] = None
    last_index: int = 0
    files: list[str] = []  # filenames to include in this message


class WSAnswerMessage(BaseModel):
    """Client → Server answer to AskUserQuestion."""

    type: str = "answer"
    session_id: str
    answers: dict[str, str]

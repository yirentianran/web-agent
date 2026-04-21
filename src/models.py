"""Shared Pydantic models for the web agent."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


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
    description: str = ""
    content: str = ""
    path: str = ""
    created_at: str = ""  # ISO 8601 timestamp
    created_by: str = ""  # "upload" | "skill-creator"



# ── Memory Models ───────────────────────────────────────────────────


class UserPreferences(BaseModel):
    model: str = "claude-sonnet-4-6"
    max_budget_usd: float = 2.0
    language: str = "zh"
    audit_detail_level: str = "standard"  # standard | detailed


class KeyContact(BaseModel):
    name: str
    role: str


class EntityMemory(BaseModel):
    company_name: str = ""
    credit_code: str = ""
    fiscal_year: str = ""
    accounting_standard: str = "CAS"  # CAS | IFRS | US GAAP
    industry: str = ""
    last_audit_date: str = ""
    key_contacts: list[KeyContact] = []


class PriorFinding(BaseModel):
    session: str
    date: str
    item: str
    standard: str
    status: str = "待整改跟踪"


class AuditContext(BaseModel):
    prior_findings: list[PriorFinding] = []
    risk_areas: list[str] = []
    prior_sessions: list[str] = []


class FileMemory(BaseModel):
    filename: str
    path: str
    last_used: str = ""


class UserMemory(BaseModel):
    user_id: str
    preferences: UserPreferences = Field(default_factory=UserPreferences)
    entity_memory: EntityMemory = Field(default_factory=EntityMemory)
    audit_context: AuditContext = Field(default_factory=AuditContext)
    file_memory: list[FileMemory] = []
    updated_at: str = ""


class MemoryUpdate(BaseModel):
    """Partial memory update — only set fields are merged."""

    preferences: Optional[dict[str, Any]] = None
    entity_memory: Optional[dict[str, Any]] = None
    audit_context: Optional[dict[str, Any]] = None
    file_memory: Optional[list[dict[str, Any]]] = None


# ── MCP Models ──────────────────────────────────────────────────────


class McpServerConfig(BaseModel):
    name: str
    type: str = "stdio"  # stdio | http
    command: Optional[str] = None
    args: list[str] = []
    url: Optional[str] = None
    env: dict[str, str] = {}
    tools: list[str] = []
    description: str = ""
    enabled: bool = True
    access: str = "all"  # all | admin

    def model_post_init(self, __context: Any) -> None:
        if self.type == "stdio" and not self.command:
            raise ValueError("command is required for stdio servers")
        if self.type == "http" and not self.url:
            raise ValueError("url is required for http servers")


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

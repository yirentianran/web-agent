"""Typed internal event protocol for agent execution pipeline.

Both modes (local SDK and container JSON) produce these typed events.
The pipeline (event_pipeline.py) consumes only InternalEvent, never raw dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class AssistantEvent:
    """Text content from the agent."""
    type: Literal["assistant"] = "assistant"
    content: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"type": "assistant", "content": self.content}


@dataclass(frozen=True)
class ToolUseEvent:
    """Tool invocation request."""
    type: Literal["tool_use"] = "tool_use"
    name: str = ""
    id: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    seq: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": "tool_use",
            "name": self.name,
            "id": self.id,
            "input": self.input,
        }
        if self.seq is not None:
            d["seq"] = self.seq
        return d


@dataclass(frozen=True)
class ToolResultEvent:
    """Tool execution result."""
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str = ""
    content: str = ""
    is_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "tool_use_id": self.tool_use_id,
            "content": self.content,
            "is_error": self.is_error,
        }


@dataclass(frozen=True)
class StreamEvent:
    """Streaming delta from agent (content_block_delta, etc.)."""
    type: Literal["stream_event"] = "stream_event"
    event: dict[str, Any] = field(default_factory=dict)
    uuid: str | None = None
    session_id: str | None = None
    index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "stream_event", "event": self.event}
        if self.uuid is not None:
            d["uuid"] = self.uuid
        if self.session_id is not None:
            d["session_id"] = self.session_id
        if self.index is not None:
            d["index"] = self.index
        return d


@dataclass(frozen=True)
class SystemEvent:
    """Lifecycle notifications (timeout, cancel, progress, session_state_changed)."""
    type: Literal["system"] = "system"
    subtype: str = ""
    status: str | None = None
    message: str | None = None
    summary: str | None = None
    usage: dict[str, Any] | None = None
    data: dict[str, Any] | None = None  # extra fields from TaskProgressMessage / SystemMessage

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "system", "subtype": self.subtype}
        if self.status is not None:
            d["status"] = self.status
        if self.message is not None:
            d["message"] = self.message
        if self.summary is not None:
            d["summary"] = self.summary
        if self.usage is not None:
            d["usage"] = self.usage
        if self.data is not None:
            d.update(self.data)
        return d


@dataclass(frozen=True)
class UserEvent:
    """User message (replayed history or new message)."""
    type: Literal["user"] = "user"
    content: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"type": "user", "content": self.content}


@dataclass(frozen=True)
class ResultEvent:
    """Final agent result (usage, stop_reason, duration)."""
    type: Literal["result"] = "result"
    subtype: str | None = None
    duration_ms: float = 0
    usage: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": "result",
            "subtype": self.subtype or "success",
            "duration_ms": self.duration_ms,
            "usage": self.usage,
        }
        if self.model:
            d["model"] = self.model
        d.update({k: v for k, v in self.raw.items() if k not in d})
        return d


@dataclass(frozen=True)
class ErrorEvent:
    """Error message for frontend display."""
    type: Literal["error"] = "error"
    message: str = ""
    subtype: str | None = None  # timeout, cancelled, general

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "error", "message": self.message}
        if self.subtype:
            d["subtype"] = self.subtype
        return d


# Discriminated union
InternalEvent = AssistantEvent | ToolUseEvent | ToolResultEvent | StreamEvent | SystemEvent | UserEvent | ResultEvent | ErrorEvent

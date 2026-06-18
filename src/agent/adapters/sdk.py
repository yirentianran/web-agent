"""Adapter: Claude Agent SDK dataclass messages → InternalEvent.

Extracts the SDK-type branches from the existing ``message_to_dicts()``
and converts each SDK message type into typed InternalEvent instances.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from typing import Any

from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    StreamEvent as SdkStreamEvent,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    UserMessage,
)

from src.agent.adapters.container_json import _process_blocks
from src.agent.protocol import (
    AssistantEvent,
    ResultEvent,
    StreamEvent,
    SystemEvent,
    UserEvent,
)


def adapt_sdk_message(
    msg: Any,
    model: str | None = None,
    tool_use_names: dict[str, str] | None = None,
) -> Iterator[Any]:  # Iterator[InternalEvent] — Any avoids circular import
    """Convert an SDK message dataclass to InternalEvent instances.

    Yields typed InternalEvent objects. The ``_process_blocks`` helper
    handles content block extraction for assistant/user messages.
    """
    from src.agent.protocol import InternalEvent  # noqa: PLC0415

    if tool_use_names is None:
        tool_use_names = {}

    if isinstance(msg, UserMessage):
        content = msg.content
        if isinstance(content, list):
            emitted: list[InternalEvent] = []
            combined_text = _process_blocks(content, emitted, tool_use_names)
            yield from emitted
            text = combined_text
        else:
            text = content
        if text:
            yield UserEvent(content=text)
        return

    if isinstance(msg, AssistantMessage):
        emitted: list[InternalEvent] = []
        combined_text = _process_blocks(msg.content, emitted, tool_use_names)
        yield from emitted
        if combined_text:
            yield AssistantEvent(content=combined_text)
        return

    if isinstance(msg, ResultMessage):
        from src.agent_result import parse_agent_result  # noqa: PLC0415

        result_data = parse_agent_result(dataclasses.asdict(msg), model=model)
        yield ResultEvent(
            subtype=result_data.get("subtype"),
            duration_ms=result_data.get("duration_ms", 0),
            usage=result_data.get("usage", {}),
            model=result_data.get("model"),
            raw=result_data,
        )
        return

    if isinstance(msg, TaskNotificationMessage):
        data: dict[str, Any] = {}
        if msg.usage:
            data["usage"] = dict(msg.usage)
            if model:
                data["usage"]["model"] = model
        if msg.summary:
            data["summary"] = msg.summary
        yield SystemEvent(
            subtype=msg.subtype or "",
            status=msg.status,
            summary=msg.summary,
            usage=data.get("usage"),
            data=data,
        )
        return

    if isinstance(msg, TaskProgressMessage):
        data: dict[str, Any] = {}
        if msg.usage:
            data["usage"] = dict(msg.usage)
            if model:
                data["usage"]["model"] = model
        if msg.data:
            data.update(msg.data)
        yield SystemEvent(subtype="progress", usage=data.get("usage"), data=data)
        return

    if isinstance(msg, SystemMessage):
        data: dict[str, Any] = {}
        if msg.data:
            data.update(msg.data)
        yield SystemEvent(subtype=msg.subtype, data=data)
        return

    if isinstance(msg, SdkStreamEvent):
        evt = msg.event if isinstance(msg.event, dict) else {}
        yield StreamEvent(
            event=evt,
            uuid=getattr(msg, "uuid", None),
            session_id=getattr(msg, "session_id", None),
            index=evt.get("index"),
        )
        return

    # Fallback: unknown type
    if hasattr(msg, "__dict__"):
        yield SystemEvent(subtype="unknown", data={"raw": msg.__dict__})

"""Adapter: container WebSocket JSON dict → InternalEvent.

Extracts the dict-type branches from the existing ``message_to_dicts()``
and converts each container JSON message into typed InternalEvent instances.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from src.agent.protocol import (
    AssistantEvent,
    ResultEvent,
    StreamEvent,
    ToolResultEvent,
    ToolUseEvent,
    UserEvent,
)


def _process_blocks(
    blocks: list[Any],
    emitted: list[Any],  # list[InternalEvent]
    tool_use_names: dict[str, str],
) -> str:
    """Process content blocks, appending ToolUseEvent/ToolResultEvent to emitted.

    Returns combined text for AssistantEvent/UserEvent content.
    Shared between sdk.py and container_json.py adapters.
    """
    from src.agent.protocol import InternalEvent  # noqa: PLC0415

    text_parts: list[str] = []
    try:
        from claude_agent_sdk.types import (
            TextBlock,
            ThinkingBlock,
            ToolResultBlock,
            ToolUseBlock,
        )
    except ImportError:
        TextBlock = ThinkingBlock = ToolUseBlock = ToolResultBlock = None

    # Test suites that mock main_server replace sys.modules["claude_agent_sdk.types"]
    # with MagicMock objects. The import above "succeeds" but produces MagicMock
    # instances, which break isinstance() below. Check every imported type.
    _imported = [TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock]
    if any(v is not None and not isinstance(v, type) for v in _imported):
        TextBlock = ThinkingBlock = ToolUseBlock = ToolResultBlock = None

    # First pass: build tool_use_names mapping
    for block in blocks:
        if ToolUseBlock and isinstance(block, ToolUseBlock):
            tool_use_names[block.id] = block.name
        elif isinstance(block, dict) and block.get("type") == "tool_use":
            tool_use_names[block.get("id", "")] = block.get("name", "")

    # Second pass: emit events
    for block in blocks:
        if TextBlock and isinstance(block, TextBlock):
            text_parts.append(block.text)
        elif ThinkingBlock and isinstance(block, ThinkingBlock):
            text_parts.append(f"[thinking] {block.thinking}[/thinking]")
        elif ToolUseBlock and isinstance(block, ToolUseBlock):
            emitted.append(ToolUseEvent(
                name=block.name,
                id=block.id,
                input=block.input,
            ))
        elif ToolResultBlock and isinstance(block, ToolResultBlock):
            tool_name = tool_use_names.get(block.tool_use_id, "")
            content = block.content
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if isinstance(item, dict):
                        parts.append(item.get("text", str(item)))
                    else:
                        parts.append(str(item))
                content = "\n".join(parts)
            emitted.append(ToolResultEvent(
                tool_use_id=block.tool_use_id,
                name=tool_name,
                content=str(content) if content else "",
                is_error=block.is_error if hasattr(block, "is_error") else False,
            ))
        elif isinstance(block, dict):
            bt = block.get("type", "")
            if bt == "text":
                text_parts.append(block.get("text", ""))
            elif bt == "thinking":
                text_parts.append(f"[thinking] {block.get('thinking', '')}[/thinking]")
            elif bt in ("tool_use", "server_tool_use"):
                emitted.append(ToolUseEvent(
                    name=block.get("name", ""),
                    id=block.get("id", ""),
                    input=block.get("input", {}),
                ))
            elif bt == "tool_result":
                tool_use_id_val = block.get("tool_use_id", "")
                tool_name = tool_use_names.get(tool_use_id_val, "")
                raw_content = block.get("content", "")
                if isinstance(raw_content, list):
                    text_parts_mcp: list[str] = []
                    has_non_text = False
                    for item in raw_content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            if not has_non_text:
                                text_parts_mcp.append(item.get("text", ""))
                        else:
                            has_non_text = True
                    if text_parts_mcp and not has_non_text:
                        content = "\n".join(text_parts_mcp)
                    else:
                        content = json.dumps(raw_content, ensure_ascii=False, indent=2)
                else:
                    content = raw_content if isinstance(raw_content, str) else str(raw_content)
                emitted.append(ToolResultEvent(
                    tool_use_id=tool_use_id_val,
                    name=tool_name,
                    content=content,
                    is_error=block.get("is_error", False),
                ))
            else:
                text_parts.append(str(block))

    return "\n".join(text_parts)


def adapt_container_message(
    data: dict[str, Any],
    model: str | None = None,
    tool_use_names: dict[str, str] | None = None,
) -> Iterator[Any]:  # Iterator[InternalEvent]
    """Convert a container WebSocket JSON dict to InternalEvent instances."""
    from src.agent.protocol import InternalEvent  # noqa: PLC0415

    if tool_use_names is None:
        tool_use_names = {}

    msg_type = data.get("type", "")

    if msg_type == "assistant":
        message = data.get("message", {})
        if message:
            content_blocks = message.get("content", [])
            emitted: list[InternalEvent] = []
            combined_text = _process_blocks(content_blocks, emitted, tool_use_names)
            yield from emitted
            if combined_text:
                yield AssistantEvent(content=combined_text)
        return

    if msg_type == "user":
        message = data.get("message", {})
        if message:
            content_blocks = message.get("content", [])
            emitted: list[InternalEvent] = []
            combined_text = _process_blocks(content_blocks, emitted, tool_use_names)
            yield from emitted
            text = combined_text
        else:
            text = data.get("content", "")
        if text:
            yield UserEvent(content=text)
        return

    if msg_type == "stream_event":
        yield StreamEvent(event=data.get("event", {}))
        return

    if msg_type == "result":
        from src.agent_result import parse_agent_result  # noqa: PLC0415

        result_data = parse_agent_result(data, model=model)
        yield ResultEvent(
            subtype=result_data.get("subtype"),
            duration_ms=result_data.get("duration_ms", 0),
            usage=result_data.get("usage", {}),
            model=result_data.get("model"),
            raw=result_data,
        )
        return

    # Unknown dict type — ignore

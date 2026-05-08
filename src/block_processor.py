"""Unified content block processing for both container and non-container modes.

Handles SDK dataclass blocks (TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock)
and JSON dict blocks ({"type": "text", ...}) from container WebSocket.
"""

from __future__ import annotations

import json
from typing import Any, Callable


def process_content_blocks(
    blocks: list[Any],
    emit: Callable[[dict[str, Any]], None],
    tool_use_names: dict[str, str] | None = None,
) -> str:
    """Process content blocks uniformly for both container and non-container modes.

    Handles SDK dataclass blocks (TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock)
    and JSON dict blocks ({"type": "text", ...}) from container WebSocket.

    Args:
        blocks: List of content blocks (SDK dataclasses or JSON dicts)
        emit: Callback to emit tool_use/tool_result messages
        tool_use_names: Optional mapping of tool_use_id -> tool name (for ToolResultBlock)

    Returns:
        Combined text from text/thinking blocks, suitable for assistant message content.
    """
    text_parts: list[str] = []

    # Lazy import SDK types to avoid circular dependency
    try:
        from claude_agent_sdk.types import (
            TextBlock,
            ThinkingBlock,
            ToolResultBlock,
            ToolUseBlock,
        )
    except ImportError:
        # SDK not available (e.g., in test environments with mocks)
        TextBlock = None
        ThinkingBlock = None
        ToolUseBlock = None
        ToolResultBlock = None

    # Build tool_use_names mapping if not provided (from SDK ToolUseBlock or dict)
    if tool_use_names is None:
        tool_use_names = {}
        for block in blocks:
            # SDK dataclass
            if ToolUseBlock and isinstance(block, ToolUseBlock):
                tool_use_names[block.id] = block.name
            # JSON dict
            elif isinstance(block, dict) and block.get("type") == "tool_use":
                tool_use_names[block.get("id", "")] = block.get("name", "")

    for block in blocks:
        # ── SDK dataclass handling ──────────────────────────────
        if TextBlock and isinstance(block, TextBlock):
            text_parts.append(block.text)
        elif ThinkingBlock and isinstance(block, ThinkingBlock):
            text_parts.append(f"[thinking] {block.thinking}[/thinking]")
        elif ToolUseBlock and isinstance(block, ToolUseBlock):
            emit({
                "type": "tool_use",
                "name": block.name,
                "id": block.id,
                "input": block.input,
            })
        elif ToolResultBlock and isinstance(block, ToolResultBlock):
            tool_name = tool_use_names.get(block.tool_use_id, "unknown")
            content_val: str
            if isinstance(block.content, list):
                content_val = json.dumps(block.content, ensure_ascii=False)
            elif block.content is None:
                content_val = ""
            else:
                content_val = block.content
            result_dict = {
                "type": "tool_result",
                "name": tool_name,
                "tool_use_id": block.tool_use_id,
                "content": content_val,
            }
            if block.is_error is not None:
                result_dict["is_error"] = block.is_error
            emit(result_dict)

        # ── JSON dict handling (container mode) ──────────────────
        elif isinstance(block, dict):
            bt = block.get("type", "")
            if bt == "text":
                text_parts.append(block.get("text", ""))
            elif bt == "thinking":
                thinking_text = block.get("thinking", "")
                text_parts.append(f"[thinking] {thinking_text}[/thinking]")
            elif bt == "tool_use":
                emit({
                    "type": "tool_use",
                    "name": block.get("name", ""),
                    "id": block.get("id", ""),
                    "input": block.get("input", {}),
                })
            elif bt == "server_tool_use":
                emit({
                    "type": "tool_use",
                    "name": block.get("name", ""),
                    "id": block.get("id", ""),
                    "input": block.get("input", {}),
                })
            elif bt == "tool_result":
                tool_name = tool_use_names.get(block.get("tool_use_id", ""), "unknown")
                emit({
                    "type": "tool_result",
                    "name": tool_name,
                    "tool_use_id": block.get("tool_use_id", ""),
                    "content": block.get("content", ""),
                })

        # ── Unknown block type ───────────────────────────────────
        else:
            text_parts.append(str(block))

    return "\n".join(text_parts)
"""Shared agent-result parsing used by both container and non-container modes.

The function accepts a dict (from CLI JSON or dataclasses.asdict(ResultMessage))
so it works without importing SDK types.
"""

from __future__ import annotations

from typing import Any


def parse_agent_result(data: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    """Convert agent result dict to frontend-compatible format.

    ``data`` may come from CLI stdout JSON (container mode) or from
    ``dataclasses.asdict(ResultMessage)`` (non-container mode).
    """
    result: dict[str, Any] = {
        "type": "result",
        "subtype": data.get("subtype", ""),
        "duration_ms": data.get("duration_ms", 0),
        "num_turns": data.get("num_turns", 0),
        "is_error": data.get("is_error", False),
    }
    usage = data.get("usage")
    if usage:
        result["usage"] = dict(usage) if isinstance(usage, dict) else usage
        if model:
            result["usage"]["model"] = model
    content = data.get("result")
    if content:
        result["content"] = content
    session_id = data.get("session_id")
    if session_id:
        result["session_id"] = session_id
    return result

"""Tool output truncation utility.

Prevents context window overflow and cost explosion from oversized tool results.
"""

from __future__ import annotations

MAX_TOOL_OUTPUT_CHARS = int(__import__("os").getenv("MAX_TOOL_OUTPUT_CHARS", "10000"))


def truncate_tool_output(raw: str, max_chars: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    """Truncate tool output to max_chars, preserving head + summary stats.

    Returns the truncated string with a summary note if truncation occurred.
    """
    if len(raw) <= max_chars:
        return raw

    head_len = max_chars - 200  # reserve space for summary
    head = raw[:head_len]
    total_chars = len(raw)
    truncated_chars = total_chars - head_len

    # Count lines for a more useful summary
    total_lines = raw.count("\n") + 1
    shown_lines = head.count("\n") + 1
    hidden_lines = total_lines - shown_lines

    return (
        f"{head}\n\n"
        f"[... truncated: {truncated_chars:,} characters, "
        f"{hidden_lines} lines hidden of {total_lines:,} total ...]"
    )

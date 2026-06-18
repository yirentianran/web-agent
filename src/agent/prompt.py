"""Prompt formatting — history compilation, language directives, attachments."""

from __future__ import annotations

import os
import re
from typing import Any

MAX_CONTINUATION_WINDOW = int(os.getenv("MAX_CONTINUATION_WINDOW", "50"))
MAX_PROMPT_LENGTH = int(os.getenv("MAX_PROMPT_LENGTH", "32000"))
_TOOL_RESULT_MAX_CHARS = int(os.getenv("TOOL_RESULT_MAX_CHARS", "500"))


def build_history_prompt(
    history: list[dict[str, Any]],
    user_message: str,
    language: str | None = None,
    session_id: str | None = None,
) -> str:
    """Build a multi-turn conversation prompt from history + new message.

    Controls:
    - Window: only the last N messages are included (configurable, default 50)
    - Truncation: tool_result content capped at TOOL_RESULT_MAX_CHARS; tool_use
      records name + brief input; assistant text included
    - Length: total prompt capped at MAX_PROMPT_LENGTH chars; oldest messages dropped first
    - System messages and empty content are skipped
    - The final user message is always preserved
    - When *language* is set, the assistant turn is primed in that language
      and Chinese assistant responses are flagged when in English mode.
    """
    parts: list[str] = []
    lang_name = "中文" if language == "zh" else "English"
    _HAS_CJK = re.compile(r"[一-鿿㐀-䶿]")

    for msg in history:
        msg_type = msg.get("type")
        content = msg.get("content", "")
        subtype = msg.get("subtype")

        # Skip system/stream messages that add no conversational value
        if msg_type == "system" and subtype != "session_state_changed":
            continue
        if msg_type in ("stream_event", "hook_started", "hook_response"):
            continue

        if msg_type == "assistant":
            if not content or not content.strip():
                continue
            # Include assistant text responses (skip thinking blocks)
            if content.startswith("[thinking]") and content.endswith("[/thinking]"):
                continue
            # When in English mode, flag Chinese assistant responses so the
            # model knows to ignore them instead of copying the pattern.
            if language == "en" and _HAS_CJK.search(content):
                parts.append(
                    f"Assistant (previous response was in Chinese — "
                    f"IGNORE this language, you must respond in English): {content}"
                )
            elif language == "zh" and not _HAS_CJK.search(content):
                parts.append(
                    f"Assistant (previous response was in English — "
                    f"IGNORE this language, you must respond in Chinese): {content}"
                )
            else:
                parts.append(f"Assistant: {content}")
        elif msg_type == "user" and (not content or not content.strip()):
            continue
        elif msg_type == "tool_use":
            name = msg.get("name", "?")
            input_data = msg.get("input")
            if input_data and isinstance(input_data, dict):
                brief = ", ".join(f"{k}={str(v)[:50]}" for k, v in list(input_data.items())[:3])
                parts.append(f"[Tool Use: {name}({brief})]")
            else:
                parts.append(f"[Tool Use: {name}]")
        elif msg_type == "tool_result":
            if not content or not content.strip():
                continue
            truncated = content[:_TOOL_RESULT_MAX_CHARS]
            if len(content) > _TOOL_RESULT_MAX_CHARS:
                truncated += "..."
            parts.append(f"[Tool Result] {truncated}")
        elif msg_type == "user":
            line = f"User: {content}"
            attached = msg.get("data")
            if attached and isinstance(attached, list):
                paths = [f"uploads/{session_id}/{f.get('filename', '?')}" for f in attached if isinstance(f, dict)]
                if paths:
                    line += f"\n(Attached files: {', '.join(paths)})"
            parts.append(line)

    # Sliding window — keep only the last N parts
    if len(parts) > MAX_CONTINUATION_WINDOW:
        parts = parts[-MAX_CONTINUATION_WINDOW:]

    # Add preamble explaining this is a continuation, then the new message
    preamble = (
        "The following is a transcript of our prior conversation in this session. "
        "Please reference this history when responding to the new message below.\n"
    )
    if language:
        preamble += (
            f"CRITICAL: You MUST respond in {lang_name}, including ALL thinking blocks, reasoning, and replies. "
            f"Any thinking or reasoning content must be in {lang_name}. "
            f"Do not copy the language of any previous assistant responses or thinking "
            f"if they are in the wrong language.\n"
        )
    preamble += "---\n"
    if session_id:
        preamble += (
            f"Your working directory is the user's workspace. "
            f"All generated files must be written to: outputs/{session_id}/\n"
        )
    # Avoid duplicating the user message — it may already be in history
    # if the sync-persist in handle_ws completed before we queried SQLite.
    last_part = parts[-1] if parts else ""
    expected = f"User: {user_message}"
    if last_part != expected:
        parts.append(expected)
    # Prime the assistant turn in the target language so the model starts
    # generating in the correct language from the first token.
    if language:
        parts.append(f"Assistant (respond and think in {lang_name} only):")
    else:
        parts.append("Assistant:")

    # Enforce total length limit by dropping from the start
    prompt = preamble + "\n\n".join(parts)
    while len(prompt) > MAX_PROMPT_LENGTH and len(parts) > 2:
        parts = parts[1:]
        prompt = "\n\n".join(parts)

    return prompt


def format_first_message_prompt(
    user_message: str,
    attached_files: list[str] | None,
    language: str | None = None,
    session_id: str | None = None,
) -> str:
    """Build the first-message prompt, including file paths if files were uploaded.

    When *language* is set, a [REMINDER] tag is prepended so the model
    follows the language directive even when the user message is in
    another language. This is critical for Chinese-native models like Qwen.
    """
    lang_name = "中文" if language == "zh" else "English"
    # Natural language priming activates the target language's generation
    # pathway more effectively than a bracketed metadata tag.
    if language == "zh":
        prefix = "请用中文回复，包括所有思考内容、推理过程和最终答复。\n\n"
    elif language == "en":
        prefix = "Please respond in English, including all thinking blocks, reasoning, and final replies.\n\n"
    else:
        prefix = ""

    session_guidance = ""
    if session_id:
        session_guidance = (
            f"Your working directory is the user's workspace. "
            f"All generated files must be written to: outputs/{session_id}/\n\n"
        )

    if not attached_files:
        return prefix + session_guidance + user_message
    paths = ", ".join(
        f if f.startswith("uploads/") or f.startswith("outputs/") else f"uploads/{session_id}/{f}" for f in attached_files
    ) if session_id else ", ".join(
        f if f.startswith("uploads/") or f.startswith("outputs/") else f"uploads/{f}" for f in attached_files
    )
    return f"{prefix}{session_guidance}{user_message}\n\n(Attached files: {paths})"

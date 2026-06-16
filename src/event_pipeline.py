"""Shared event-processing pipeline used by both container and non-container modes.

The container bridge feeds raw WS JSON dicts into ``message_to_dicts``, which
now accepts both SDK dataclass objects and plain dicts.  Per-event processing
(truncation, observation recording, skill tracking, buffer writes) and post-loop
teardown live here so the two code paths stay in sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.file_utils import build_download_url, should_include_generated_file
from src.skill_manager import record_skill_usage_from_event
from src.truncation import maybe_truncate_tool_result_content
from src.workspace_enforcement import normalize_write_path


@dataclass(frozen=True)
class EventContext:
    """Immutable context carried through every ``process_event`` call."""

    user_id: str
    session_id: str
    buffer: Any  # MessageBuffer — avoid circular import
    observer: Any | None  # ToolObserver | None
    skill_manager: Any | None
    generated_files: list[dict] = field(default_factory=list)


async def process_event(ctx: EventContext, event: dict[str, Any]) -> None:
    """Process a single event dict: skip, truncate, track, buffer, observe.

    Called by both ``run_agent_task`` (non-container) and the container bridge
    after ``message_to_dicts`` has converted the raw message into standard
    event dicts.
    """
    etype = event.get("type", "")

    # User messages are persisted before the agent task starts; duplicates
    # from the agent response (e.g. replayed history) must be skipped.
    if etype == "user":
        return

    # AskUserQuestion is handled by _can_use_tool_for_session (non-container)
    # or _handle_permission_check (container bridge).  The tool_use has already
    # been buffered at that point, so skip the duplicate here.
    if etype == "tool_use" and event.get("name") == "AskUserQuestion":
        return

    # ── skill usage tracking ──────────────────────────────────────
    if ctx.skill_manager is not None:
        await record_skill_usage_from_event(
            event,
            ctx.skill_manager,
            user_id=ctx.user_id,
            session_id=ctx.session_id,
        )

    # ── Write file tracking (generated files) ─────────────────────
    if etype == "tool_use" and event.get("name") == "Write":
        _track_write_file(event, ctx)

    # ── tool_result truncation ────────────────────────────────────
    event_to_buffer = event
    if etype == "tool_result":
        event_to_buffer = {
            **event,
            "content": maybe_truncate_tool_result_content(event.get("content", "")),
        }

    # ── persist ───────────────────────────────────────────────────
    await ctx.buffer.add_message(ctx.session_id, event_to_buffer, ctx.user_id)

    # ── observation recording ─────────────────────────────────────
    if ctx.observer is not None:
        if etype == "tool_use":
            await ctx.observer.on_tool_use(
                event.get("id", ""),
                event.get("name", ""),
                event.get("input", {}),
                message_seq=event.get("seq"),
            )
        elif etype == "tool_result":
            await ctx.observer.on_tool_result(
                event.get("tool_use_id", ""),
                is_error=event.get("is_error", False),
            )


def _track_write_file(event: dict[str, Any], ctx: EventContext) -> None:
    """Extract file metadata from a Write tool_use and append to generated_files."""
    tool_input = event.get("input") or {}
    file_path = tool_input.get("file_path", "")
    if file_path:
        file_path = normalize_write_path(file_path, ctx.session_id)
        tool_input["file_path"] = file_path  # write back so persisted event has normalized path
    if not file_path or not should_include_generated_file(Path(file_path).name):
        return

    # Skill-internal files are not user-facing.
    if ".claude/skills/" in file_path or "shared-skills/" in file_path:
        return

    display_name = Path(file_path).name
    content = tool_input.get("content", "")
    try:
        size = len(content.encode("utf-8"))
    except Exception:
        size = len(content)

    download_url = build_download_url(ctx.user_id, file_path, directory="outputs")
    entry = {
        "filename": display_name,
        "size": size,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "download_url": download_url,
    }

    # Dedup: keep only the latest version for the same filename.
    dup_idx = next(
        (i for i, g in enumerate(ctx.generated_files) if g["filename"] == display_name),
        None,
    )
    if dup_idx is not None:
        ctx.generated_files[dup_idx] = entry
        return

    ctx.generated_files.append(entry)

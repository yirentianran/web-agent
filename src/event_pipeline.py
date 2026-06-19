"""Shared event-processing pipeline used by both container and non-container modes.

The container bridge feeds raw WS JSON dicts into ``message_to_dicts``, which
now accepts both SDK dataclass objects and plain dicts.  Per-event processing
(truncation, observation recording, skill tracking, buffer writes) and post-loop
teardown live here so the two code paths stay in sync.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from src.agent.protocol import InternalEvent
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


async def process_event(ctx: EventContext, event: InternalEvent | dict[str, Any]) -> None:
    """Process a single event: skip, truncate, track, buffer, observe.

    Called by both LocalAgentExecutor (passes InternalEvent) and the
    container bridge (passes dict from .to_dict()). Normalizes to dict
    access for backward compatibility.
    """
    if not isinstance(event, dict):
        event = event.to_dict()

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


async def _finish_task(
    session_id: str,
    user_id: str,
    buffer: Any,
    workspace: Any,
    session_store: Any,
    skill_manager: Any,
    obs_store: Any,
    agent_log: Any,
    pre_scan_snapshot: set[str],
    result_event: dict[str, Any] | None,
    language: str | None,
) -> None:
    """Post-loop teardown shared by container and non-container modes.

    State=completed is emitted before title generation so the spinner stops
    immediately — title generation may call an LLM and adds latency that
    isn't part of the agent's actual work.
    """
    from main_server import (  # noqa: PLC0415
        _auto_generate_title,
        _emit_file_result,
        _scan_workspace_for_generated_files,
        _summarize_and_store_session,
    )

    generated_files = await _scan_workspace_for_generated_files(
        workspace, user_id, session_id, exclude_paths=pre_scan_snapshot,
    )

    await _emit_file_result(user_id, session_id, workspace, generated_files, buffer)

    # Emit completed before title generation — title gen may call an LLM
    # and the spinner should stop before that latency.
    await buffer.add_message(
        session_id,
        {"type": "system", "subtype": "session_state_changed", "state": "completed"},
        user_id,
    )

    # Result metadata — after completed so footer renders in order
    if result_event is not None:
        await buffer.add_message(session_id, result_event, user_id)

    await _auto_generate_title(session_id, user_id, buffer, session_store, language)

    # Mark done
    await buffer.mark_done(session_id)
    agent_log.end_session(session_id, status="completed")

    # Observations + background tasks
    if obs_store:
        await obs_store.record(
            session_id=session_id, user_id=user_id,
            event_type="session_complete", success=True,
        )
    asyncio.create_task(_summarize_and_store_session(session_id, user_id))
    if skill_manager is not None:
        asyncio.create_task(skill_manager.migrate_from_filesystem())


async def handle_task_error(
    error: Exception,
    *,
    session_id: str,
    user_id: str,
    buffer: Any,
    obs_store: Any,
    agent_log: Any,
    cleanup_fn: Any | None = None,
) -> None:
    """Shared error handling for both local and container executors.

    Handles: TimeoutError, asyncio.CancelledError, and generic Exception.
    Emits appropriate error messages, state changes, and marks session done.

    Note: ``agent_log`` may be None when the logger was not yet initialized
    (e.g., early failures before ``AgentLogger`` construction).
    """

    if isinstance(error, TimeoutError):
        logger.error("Agent task %s: timeout", session_id)
        if cleanup_fn is not None:
            try:
                await cleanup_fn(session_id)
            except Exception:
                pass
        await buffer.add_message(
            session_id,
            {
                "type": "system",
                "subtype": "session_timeout",
                "message": "Agent task timed out. The agent may be stuck processing a file.",
            },
            user_id,
        )
        await buffer.add_message(
            session_id,
            {"type": "system", "subtype": "session_state_changed", "state": "error"},
            user_id,
        )
        await buffer.mark_done(session_id)
        if agent_log is not None:
            agent_log.end_session(session_id, status="timeout")
        if obs_store:
            await obs_store.record(
                session_id=session_id,
                user_id=user_id,
                event_type="session_error",
                success=False,
                error_message="timeout",
            )

    elif isinstance(error, asyncio.CancelledError):
        await buffer.add_message(
            session_id,
            {
                "type": "system",
                "subtype": "session_cancelled",
                "message": "Session cancelled by user.",
            },
            user_id,
        )
        await buffer.add_message(
            session_id,
            {"type": "system", "subtype": "session_state_changed", "state": "cancelled"},
            user_id,
        )
        await buffer.mark_done(session_id)
        if agent_log is not None:
            agent_log.end_session(session_id, status="cancelled")
        if obs_store:
            await obs_store.record(
                session_id=session_id,
                user_id=user_id,
                event_type="user_interrupt",
                success=False,
            )

    else:
        error_msg = str(error)
        if "JSON message exceeded maximum buffer size" in error_msg:
            logger.warning(
                "Agent task %s: buffer overflow — %s", session_id, error_msg
            )
            error_msg = (
                "A tool produced too much output and was truncated to avoid "
                "overwhelming the system. Try narrowing your request or "
                "processing the data in smaller steps."
            )
        else:
            logger.exception(
                "Agent task %s: unexpected error type=%s: %s",
                session_id,
                type(error).__name__,
                error,
            )
        if cleanup_fn is not None:
            try:
                await cleanup_fn(session_id)
            except Exception:
                pass
        await buffer.add_message(
            session_id,
            {"type": "error", "message": error_msg},
            user_id,
        )
        await buffer.add_message(
            session_id,
            {"type": "system", "subtype": "session_state_changed", "state": "error"},
            user_id,
        )
        await buffer.mark_done(session_id)
        if agent_log is not None:
            agent_log.end_session(session_id, status="error")
        if obs_store:
            await obs_store.record(
                session_id=session_id,
                user_id=user_id,
                event_type="session_error",
                success=False,
                error_message=str(error)[:500],
            )

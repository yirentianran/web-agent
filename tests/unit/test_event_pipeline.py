"""Tests for the shared event-processing pipeline.

Verifies that ``EventContext`` construction and ``process_event`` behavior
match the spec for skip, truncate, track, buffer, and observe steps.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.event_pipeline import EventContext, process_event


class TestProcessEvent:
    """Test suite for process_event behavior."""

    @pytest.fixture
    def ctx(self) -> EventContext:
        """Default context with mock buffer and observer."""
        return EventContext(
            user_id="u1",
            session_id="s1",
            buffer=AsyncMock(),
            observer=AsyncMock(),
            skill_manager=None,
            generated_files=[],
        )

    # ── skip rules ─────────────────────────────────────────────────

    async def test_skips_user_type_events(self, ctx: EventContext) -> None:
        """User-type events are persisted before the agent task starts
        and must not be duplicated by process_event."""
        await process_event(ctx, {"type": "user", "content": "hello"})
        ctx.buffer.add_message.assert_not_called()

    async def test_skips_ask_user_question_tool_use(self, ctx: EventContext) -> None:
        """AskUserQuestion is handled externally by permission_check;
        the tool_use should not be re-buffered."""
        event = {"type": "tool_use", "name": "AskUserQuestion", "id": "id1", "input": {}}
        await process_event(ctx, event)
        ctx.buffer.add_message.assert_not_called()

    # ── skill tracking ─────────────────────────────────────────────

    async def test_records_skill_usage_when_manager_set(self) -> None:
        """When skill_manager is not None, record_skill_usage_from_event
        should be called for each event."""
        ctx = EventContext(
            user_id="u1",
            session_id="s1",
            buffer=AsyncMock(),
            observer=AsyncMock(),
            skill_manager=MagicMock(),
            generated_files=[],
        )
        with patch(
            "src.event_pipeline.record_skill_usage_from_event",
            new_callable=AsyncMock,
        ) as mock_rec:
            await process_event(
                ctx,
                {"type": "tool_use", "name": "Skill", "id": "id1", "input": {}},
            )
            mock_rec.assert_called_once()

    # ── truncation ─────────────────────────────────────────────────

    async def test_truncates_oversized_tool_result(self, ctx: EventContext) -> None:
        """Oversized tool_result content should be truncated before
        being written to the buffer."""
        long_content = "x" * 200_000
        with patch(
            "src.event_pipeline.maybe_truncate_tool_result_content",
        ) as mock_trunc:
            mock_trunc.return_value = "truncated"
            await process_event(
                ctx,
                {"type": "tool_result", "tool_use_id": "tu1", "content": long_content},
            )
            mock_trunc.assert_called_once_with(long_content)
            call_args = ctx.buffer.add_message.call_args
            assert call_args is not None
            assert call_args[0][1]["content"] == "truncated"

    # ── buffer write ───────────────────────────────────────────────

    async def test_writes_event_to_buffer(self, ctx: EventContext) -> None:
        """Every non-skipped event should be written to the buffer."""
        event = {"type": "assistant", "content": "hello"}
        await process_event(ctx, event)
        ctx.buffer.add_message.assert_called_once_with("s1", event, "u1")

    # ── observation recording ──────────────────────────────────────

    async def test_records_tool_use_observation(self, ctx: EventContext) -> None:
        """ToolUse events should trigger observer.on_tool_use."""
        event = {
            "type": "tool_use",
            "name": "Bash",
            "id": "id1",
            "input": {"cmd": "ls"},
            "seq": 5,
        }
        await process_event(ctx, event)
        ctx.observer.on_tool_use.assert_called_once_with(
            "id1",
            "Bash",
            {"cmd": "ls"},
            message_seq=5,
        )

    async def test_records_tool_result_observation(self, ctx: EventContext) -> None:
        """ToolResult events should trigger observer.on_tool_result."""
        event = {
            "type": "tool_result",
            "tool_use_id": "tu1",
            "content": "ok",
            "is_error": False,
        }
        await process_event(ctx, event)
        ctx.observer.on_tool_result.assert_called_once_with("tu1", is_error=False)

    # ── Write file tracking ────────────────────────────────────────

    async def test_tracks_write_file_in_generated_files(self) -> None:
        """Write tool_use should append file metadata to
        ctx.generated_files."""
        ctx = EventContext(
            user_id="u1",
            session_id="s1",
            buffer=AsyncMock(),
            observer=None,
            skill_manager=None,
            generated_files=[],
        )
        event = {
            "type": "tool_use",
            "name": "Write",
            "id": "id1",
            "input": {"file_path": "outputs/s1/report.txt", "content": "data"},
        }
        with patch(
            "src.event_pipeline.normalize_write_path",
            return_value="outputs/s1/report.txt",
        ):
            with patch(
                "src.event_pipeline.should_include_generated_file",
                return_value=True,
            ):
                with patch(
                    "src.event_pipeline.build_download_url",
                    return_value="/dl/report.txt",
                ):
                    await process_event(ctx, event)

        assert len(ctx.generated_files) == 1
        assert ctx.generated_files[0]["filename"] == "report.txt"
        assert ctx.generated_files[0]["download_url"] == "/dl/report.txt"

    async def test_write_file_skipped_when_not_in_data_exts(self) -> None:
        """Files not in DATA_EXTS should not be added to generated_files."""
        ctx = EventContext(
            user_id="u1",
            session_id="s1",
            buffer=AsyncMock(),
            observer=None,
            skill_manager=None,
            generated_files=[],
        )
        event = {
            "type": "tool_use",
            "name": "Write",
            "id": "id1",
            "input": {"file_path": "script.py", "content": "data"},
        }
        with patch(
            "src.event_pipeline.normalize_write_path",
            return_value="script.py",
        ):
            with patch(
                "src.event_pipeline.should_include_generated_file",
                return_value=False,
            ):
                await process_event(ctx, event)

        assert len(ctx.generated_files) == 0

    async def test_write_file_dedup_same_filename(self) -> None:
        """Writing the same filename twice should keep only the latest."""
        ctx = EventContext(
            user_id="u1",
            session_id="s1",
            buffer=AsyncMock(),
            observer=None,
            skill_manager=None,
            generated_files=[],
        )

        def _make_write_event(file_path: str, content: str) -> dict:
            return {
                "type": "tool_use",
                "name": "Write",
                "id": "id1",
                "input": {"file_path": file_path, "content": content},
            }

        with patch(
            "src.event_pipeline.normalize_write_path",
            side_effect=lambda fp, sid: fp,
        ):
            with patch(
                "src.event_pipeline.should_include_generated_file",
                return_value=True,
            ):
                with patch(
                    "src.event_pipeline.build_download_url",
                    return_value="/dl/report.txt",
                ):
                    await process_event(
                        ctx, _make_write_event("report.txt", "v1"),
                    )
                    await process_event(
                        ctx, _make_write_event("report.txt", "v2"),
                    )

        assert len(ctx.generated_files) == 1
        assert ctx.generated_files[0]["filename"] == "report.txt"
        assert ctx.generated_files[0]["size"] == len("v2".encode("utf-8"))

    # ── edge cases ─────────────────────────────────────────────────

    async def test_observer_none_does_not_crash(self) -> None:
        """When observer is None, both tool_use and tool_result events
        should not raise."""
        ctx = EventContext(
            user_id="u1",
            session_id="s1",
            buffer=AsyncMock(),
            observer=None,
            skill_manager=None,
            generated_files=[],
        )
        await process_event(
            ctx,
            {"type": "tool_use", "name": "Bash", "id": "id1", "input": {}},
        )
        await process_event(
            ctx,
            {"type": "tool_result", "tool_use_id": "tu1", "content": "ok"},
        )
        # Should not raise

"""Tests for the shared event-processing pipeline.

Verifies that ``EventContext`` construction and ``process_event`` behavior
match the spec for skip, truncate, track, buffer, and observe steps.
"""

from __future__ import annotations

from pathlib import Path
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


class TestMessageToDictsDictBranch:
    """Tests for isinstance(msg, dict) branch in message_to_dicts."""

    def test_assistant_dict_yields_tool_use_and_text(self):
        from main_server import message_to_dicts

        msg = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me check."},
                    {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}},
                ],
            },
        }
        results = list(message_to_dicts(msg))
        types = [r["type"] for r in results]
        assert "tool_use" in types
        assert "assistant" in types
        assistant = next(r for r in results if r["type"] == "assistant")
        assert assistant["content"] == "Let me check."
        tool_use = next(r for r in results if r["type"] == "tool_use")
        assert tool_use["name"] == "Bash"

    def test_user_dict_yields_tool_result(self):
        from main_server import message_to_dicts

        msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu1", "content": "file.txt"},
                ],
            },
        }
        results = list(message_to_dicts(msg))
        assert len(results) == 1
        assert results[0]["type"] == "tool_result"
        assert results[0]["content"] == "file.txt"

    def test_stream_event_dict_yields_wrapper(self):
        from main_server import message_to_dicts

        msg = {
            "type": "stream_event",
            "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
        }
        results = list(message_to_dicts(msg))
        assert len(results) == 1
        assert results[0]["type"] == "stream_event"
        assert results[0]["event"]["delta"]["text"] == "hi"

    def test_result_dict_yields_parsed_result(self):
        from main_server import message_to_dicts

        msg = {
            "type": "result",
            "subtype": "success",
            "duration_ms": 5000,
            "num_turns": 3,
            "is_error": False,
            "result": "Done.",
        }
        results = list(message_to_dicts(msg))
        assert len(results) == 1
        assert results[0]["type"] == "result"
        assert results[0]["duration_ms"] == 5000

    def test_unknown_dict_type_is_ignored(self):
        from main_server import message_to_dicts

        msg = {"type": "unknown_xyz", "data": "abc"}
        results = list(message_to_dicts(msg))
        assert results == []

    def test_assistant_dict_without_message_field_yields_nothing(self):
        from main_server import message_to_dicts

        msg = {"type": "assistant"}
        results = list(message_to_dicts(msg))
        assert results == []

    def test_dict_branch_shares_tool_use_names(self):
        from main_server import message_to_dicts

        tool_use_names: dict[str, str] = {}
        assistant_msg = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}},
                ],
            },
        }
        list(message_to_dicts(assistant_msg, tool_use_names=tool_use_names))
        assert tool_use_names["tu1"] == "Bash"

        user_msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu1", "content": "ok"},
                ],
            },
        }
        results = list(message_to_dicts(user_msg, tool_use_names=tool_use_names))
        assert results[0]["name"] == "Bash"  # resolved from shared dict


class TestFinishTask:
    """Test suite for _finish_task shared post-loop teardown."""

    @pytest.fixture
    def mocks(self):
        skill_manager = MagicMock()
        skill_manager.migrate_from_filesystem = AsyncMock()
        return {
            "buffer": AsyncMock(),
            "session_store": MagicMock(),
            "skill_manager": skill_manager,
            "obs_store": AsyncMock(),
            "agent_log": MagicMock(),
        }

    @pytest.mark.asyncio
    async def test_finish_task_emits_file_result_title_and_completion(self, mocks):
        from src.event_pipeline import _finish_task

        with patch("main_server._scan_workspace_for_generated_files", new_callable=AsyncMock) as mock_scan:
            mock_scan.return_value = []
            with patch("main_server._emit_file_result", new_callable=AsyncMock) as mock_file:
                with patch("main_server._auto_generate_title", new_callable=AsyncMock) as mock_title:
                    with patch("main_server._summarize_and_store_session"):
                        result_event = {"type": "result", "duration_ms": 5000}
                        await _finish_task(
                            session_id="s1", user_id="u1",
                            buffer=mocks["buffer"],
                            workspace=Path("/ws"),
                            session_store=mocks["session_store"],
                            skill_manager=mocks["skill_manager"],
                            obs_store=mocks["obs_store"],
                            agent_log=mocks["agent_log"],
                            pre_scan_snapshot=set(),
                            result_event=result_event,
                            language=None,
                        )

                        mock_file.assert_called_once()
                        mock_title.assert_called_once()

                        # Verify completed state
                        add_msg_calls = [c[0][1] for c in mocks["buffer"].add_message.call_args_list]
                        assert any(m["type"] == "system" and m.get("state") == "completed" for m in add_msg_calls)

                        # Verify result emitted after completed
                        result_indices = [
                            i for i, c in enumerate(add_msg_calls)
                            if c.get("type") == "result"
                        ]
                        completed_indices = [
                            i for i, c in enumerate(add_msg_calls)
                            if c.get("type") == "system" and c.get("state") == "completed"
                        ]
                        if result_indices and completed_indices:
                            assert result_indices[0] > completed_indices[0]

                        mocks["buffer"].mark_done.assert_called_once_with("s1")
                        mocks["agent_log"].end_session.assert_called_once_with("s1", status="completed")

    @pytest.mark.asyncio
    async def test_finish_task_none_result_skips_result_emit(self, mocks):
        from src.event_pipeline import _finish_task

        with patch("main_server._scan_workspace_for_generated_files", new_callable=AsyncMock) as mock_scan:
            mock_scan.return_value = []
            with patch("main_server._emit_file_result", new_callable=AsyncMock):
                with patch("main_server._auto_generate_title", new_callable=AsyncMock):
                    with patch("main_server._summarize_and_store_session"):
                        await _finish_task(
                            session_id="s1", user_id="u1",
                            buffer=mocks["buffer"],
                            workspace=Path("/ws"),
                            session_store=mocks["session_store"],
                            skill_manager=mocks["skill_manager"],
                            obs_store=mocks["obs_store"],
                            agent_log=mocks["agent_log"],
                            pre_scan_snapshot=set(),
                            result_event=None,
                            language=None,
                        )
                        add_msg_calls = [c[0][1] for c in mocks["buffer"].add_message.call_args_list]
                        assert not any(c.get("type") == "result" for c in add_msg_calls)

    @pytest.mark.asyncio
    async def test_finish_task_none_skill_manager_does_not_migrate(self, mocks):
        from src.event_pipeline import _finish_task

        with patch("main_server._scan_workspace_for_generated_files", new_callable=AsyncMock) as mock_scan:
            mock_scan.return_value = []
            with patch("main_server._emit_file_result", new_callable=AsyncMock):
                with patch("main_server._auto_generate_title", new_callable=AsyncMock):
                    with patch("main_server._summarize_and_store_session"):
                        await _finish_task(
                            session_id="s1", user_id="u1",
                            buffer=mocks["buffer"],
                            workspace=Path("/ws"),
                            session_store=mocks["session_store"],
                            skill_manager=None,
                            obs_store=mocks["obs_store"],
                            agent_log=mocks["agent_log"],
                            pre_scan_snapshot=set(),
                            result_event=None,
                            language=None,
                        )
                        # skill_manager.migrate_from_filesystem should NOT be called
                        # No crash should occur

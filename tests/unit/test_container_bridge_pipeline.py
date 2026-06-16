"""Integration tests verifying the container bridge pipeline delegation.

Tests that container WebSocket JSON dicts flow correctly through
``message_to_dicts`` -> ``process_event`` end-to-end.
"""

import pytest
from unittest.mock import AsyncMock

from src.event_pipeline import EventContext, process_event


class TestContainerBridgePipeline:
    """Verify the bridge correctly delegates to message_to_dicts + process_event."""

    @pytest.mark.asyncio
    async def test_assistant_dict_flows_through_pipeline(self):
        """Bridge receives assistant WS dict -> message_to_dicts -> process_event."""
        ctx = EventContext(
            user_id="u1", session_id="s1",
            buffer=AsyncMock(), observer=None,
            skill_manager=None, generated_files=[],
        )

        assistant_msg = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I'll help."},
                ],
            },
        }

        from main_server import message_to_dicts

        events = list(message_to_dicts(assistant_msg))
        for event in events:
            await process_event(ctx, event)

        add_calls = [c[0][1] for c in ctx.buffer.add_message.call_args_list]
        assert any(c["type"] == "assistant" and c["content"] == "I'll help." for c in add_calls)

    @pytest.mark.asyncio
    async def test_user_dict_with_tool_result_flows_through_pipeline(self):
        """Bridge receives user WS dict -> tool_result emitted -> process_event."""
        ctx = EventContext(
            user_id="u1", session_id="s1",
            buffer=AsyncMock(), observer=AsyncMock(),
            skill_manager=None, generated_files=[],
        )

        user_msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu1", "content": "output"},
                ],
            },
        }

        from main_server import message_to_dicts

        events = list(message_to_dicts(user_msg))
        for event in events:
            await process_event(ctx, event)

        add_calls = [c[0][1] for c in ctx.buffer.add_message.call_args_list]
        assert any(c["type"] == "tool_result" for c in add_calls)

    @pytest.mark.asyncio
    async def test_result_dict_stored_for_deferred_emission(self):
        """Result events are captured for deferred emission by _finish_task."""
        result_msg = {
            "type": "result",
            "subtype": "success",
            "duration_ms": 3000,
            "num_turns": 2,
            "is_error": False,
        }

        from main_server import message_to_dicts

        events = list(message_to_dicts(result_msg))
        assert len(events) == 1
        assert events[0]["type"] == "result"
        assert events[0]["duration_ms"] == 3000

    @pytest.mark.asyncio
    async def test_stream_event_dict_preserved(self):
        """Stream events pass through message_to_dicts unchanged in structure."""
        stream_msg = {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "streaming..."},
            },
        }

        from main_server import message_to_dicts

        events = list(message_to_dicts(stream_msg))
        assert len(events) == 1
        assert events[0]["type"] == "stream_event"
        assert events[0]["event"]["delta"]["text"] == "streaming..."

    @pytest.mark.asyncio
    async def test_tool_use_names_shared_across_messages(self):
        """tool_use_names dict survives across assistant -> user message boundaries."""
        from main_server import message_to_dicts

        tool_use_names: dict[str, str] = {}

        # Assistant message defines the tool
        list(message_to_dicts({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu_abc", "name": "Grep", "input": {}},
                ],
            },
        }, tool_use_names=tool_use_names))
        assert tool_use_names["tu_abc"] == "Grep"

        # User message with tool_result resolves the name
        results = list(message_to_dicts({
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_abc", "content": "results..."},
                ],
            },
        }, tool_use_names=tool_use_names))
        assert results[0]["name"] == "Grep"  # resolved, not "unknown"

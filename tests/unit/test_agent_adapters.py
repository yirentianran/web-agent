"""Tests for SDK and container JSON adapters -> InternalEvent conversion."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from claude_agent_sdk.types import (
    StreamEvent as SdkStreamEvent,
    UserMessage,
)

from src.agent.adapters.container_json import (
    _process_blocks,
    adapt_container_message,
)
from src.agent.adapters.sdk import adapt_sdk_message
from src.agent.protocol import (
    AssistantEvent,
    ResultEvent,
    StreamEvent,
    ToolUseEvent,
    UserEvent,
)


class TestAdaptContainerMessage:
    """Tests for adapt_container_message -- dict-based container JSON input."""

    def test_assistant_with_text_content(self) -> None:
        data = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Hello from container"}],
            },
        }
        events = list(adapt_container_message(data))
        assert len(events) == 1
        assert isinstance(events[0], AssistantEvent)
        assert events[0].content == "Hello from container"

    def test_assistant_with_tool_use_block(self) -> None:
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Bash", "id": "tu1", "input": {"command": "ls"}},
                ],
            },
        }
        events = list(adapt_container_message(data))
        assert len(events) == 1
        assert isinstance(events[0], ToolUseEvent)
        assert events[0].name == "Bash"

    def test_stream_event(self) -> None:
        data = {
            "type": "stream_event",
            "event": {"type": "content_block_delta", "delta": {"text": "hi"}},
        }
        events = list(adapt_container_message(data))
        assert len(events) == 1
        assert isinstance(events[0], StreamEvent)

    def test_user_message(self) -> None:
        data = {"type": "user", "message": {"content": [{"type": "text", "text": "query"}]}}
        events = list(adapt_container_message(data))
        assert len(events) == 1
        assert isinstance(events[0], UserEvent)
        assert events[0].content == "query"

    def test_result_message(self) -> None:
        data = {
            "type": "result",
            "subtype": "success",
            "duration_ms": 500,
            "usage": {"input_tokens": 10},
            "is_error": False,
        }
        with patch("src.agent_result.parse_agent_result", return_value={
            "type": "result", "subtype": "success", "duration_ms": 500,
            "usage": {"input_tokens": 10},
        }):
            events = list(adapt_container_message(data))
            assert len(events) == 1
            assert isinstance(events[0], ResultEvent)

    def test_unknown_type_ignored(self) -> None:
        events = list(adapt_container_message({"type": "unknown_xyz", "data": {}}))
        assert len(events) == 0


class TestAdaptSdkMessage:
    """Tests for adapt_sdk_message -- SDK dataclass input."""

    def test_stream_event_conversion(self) -> None:
        """Verify SDK StreamEvent is converted correctly."""
        sdk_event = SdkStreamEvent(
            event={"type": "content_block_delta", "delta": {"text": "hi"}},
            uuid="uuid-1",
            session_id="s1",
        )
        events = list(adapt_sdk_message(sdk_event))
        assert len(events) == 1
        assert isinstance(events[0], StreamEvent)
        assert events[0].uuid == "uuid-1"
        assert events[0].session_id == "s1"

    def test_user_message_with_text_content(self) -> None:
        msg = UserMessage(content="hello")
        events = list(adapt_sdk_message(msg))
        assert len(events) == 1
        assert isinstance(events[0], UserEvent)
        assert events[0].content == "hello"


class TestProcessBlocks:
    """Tests for _process_blocks helper."""

    def test_process_text_block_dict(self) -> None:
        blocks = [{"type": "text", "text": "hello world"}]
        emitted: list = []
        names: dict[str, str] = {}
        result = _process_blocks(blocks, emitted, names)
        assert result == "hello world"
        assert len(emitted) == 0

    def test_process_tool_use_block_dict(self) -> None:
        blocks = [{"type": "tool_use", "name": "Bash", "id": "tu1", "input": {}}]
        emitted: list = []
        names: dict[str, str] = {}
        result = _process_blocks(blocks, emitted, names)
        assert result == ""
        assert len(emitted) == 1
        assert isinstance(emitted[0], ToolUseEvent)
        assert emitted[0].name == "Bash"
        assert names == {"tu1": "Bash"}

    def test_process_mixed_blocks(self) -> None:
        blocks = [
            {"type": "text", "text": "Let me run: "},
            {"type": "tool_use", "name": "Bash", "id": "tu1", "input": {}},
        ]
        emitted: list = []
        names: dict[str, str] = {}
        result = _process_blocks(blocks, emitted, names)
        assert result == "Let me run: "
        assert len(emitted) == 1

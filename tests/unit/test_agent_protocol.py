"""Tests for InternalEvent protocol types and to_dict serialization."""

from __future__ import annotations

from src.agent.protocol import (
    AssistantEvent,
    ErrorEvent,
    ResultEvent,
    StreamEvent,
    SystemEvent,
    ToolResultEvent,
    ToolUseEvent,
    UserEvent,
)


class TestAssistantEvent:
    def test_to_dict_basic(self) -> None:
        event = AssistantEvent(content="hello world")
        assert event.to_dict() == {"type": "assistant", "content": "hello world"}

    def test_to_dict_empty_content(self) -> None:
        event = AssistantEvent()
        assert event.to_dict() == {"type": "assistant", "content": ""}


class TestToolUseEvent:
    def test_to_dict_full(self) -> None:
        event = ToolUseEvent(
            name="Write",
            id="tool_001",
            input={"file_path": "outputs/hello.txt", "content": "hi"},
            seq=5,
        )
        result = event.to_dict()
        assert result["type"] == "tool_use"
        assert result["name"] == "Write"
        assert result["id"] == "tool_001"
        assert result["input"] == {"file_path": "outputs/hello.txt", "content": "hi"}
        assert result["seq"] == 5

    def test_to_dict_no_seq(self) -> None:
        event = ToolUseEvent(name="Bash", id="tool_002", input={"command": "ls"})
        result = event.to_dict()
        assert "seq" not in result


class TestToolResultEvent:
    def test_to_dict_success(self) -> None:
        event = ToolResultEvent(
            tool_use_id="tool_001", content="Output: 42", is_error=False
        )
        assert event.to_dict() == {
            "type": "tool_result",
            "tool_use_id": "tool_001",
            "content": "Output: 42",
            "is_error": False,
        }

    def test_to_dict_error(self) -> None:
        event = ToolResultEvent(
            tool_use_id="tool_001", content="Permission denied", is_error=True
        )
        assert event.to_dict()["is_error"] is True


class TestStreamEvent:
    def test_to_dict_basic(self) -> None:
        event = StreamEvent(event={"type": "content_block_delta", "delta": {}})
        result = event.to_dict()
        assert result["type"] == "stream_event"
        assert result["event"]["type"] == "content_block_delta"

    def test_to_dict_with_metadata(self) -> None:
        event = StreamEvent(
            event={"type": "content_block_delta", "delta": {}},
            uuid="abc-123",
            session_id="s1",
            index=42,
        )
        result = event.to_dict()
        assert result["uuid"] == "abc-123"
        assert result["session_id"] == "s1"
        assert result["index"] == 42


class TestSystemEvent:
    def test_to_dict_minimal(self) -> None:
        event = SystemEvent(subtype="session_state_changed", status="completed")
        assert event.to_dict() == {
            "type": "system",
            "subtype": "session_state_changed",
            "status": "completed",
        }

    def test_to_dict_with_extra_data(self) -> None:
        event = SystemEvent(subtype="progress", data={"elapsed_sec": 1.5})
        result = event.to_dict()
        assert result["elapsed_sec"] == 1.5

    def test_to_dict_with_usage(self) -> None:
        event = SystemEvent(
            subtype="progress",
            usage={"input_tokens": 100, "output_tokens": 50},
        )
        result = event.to_dict()
        assert result["usage"] == {"input_tokens": 100, "output_tokens": 50}


class TestUserEvent:
    def test_to_dict(self) -> None:
        event = UserEvent(content="Hello, can you help me?")
        assert event.to_dict() == {
            "type": "user",
            "content": "Hello, can you help me?",
        }


class TestResultEvent:
    def test_to_dict_basic(self) -> None:
        event = ResultEvent(
            subtype="success",
            duration_ms=1234.5,
            usage={"input_tokens": 100, "output_tokens": 50},
            model="claude-sonnet-4-6",
        )
        result = event.to_dict()
        assert result["type"] == "result"
        assert result["subtype"] == "success"
        assert result["duration_ms"] == 1234.5
        assert result["model"] == "claude-sonnet-4-6"


class TestErrorEvent:
    def test_to_dict_basic(self) -> None:
        event = ErrorEvent(message="Something went wrong")
        assert event.to_dict() == {
            "type": "error",
            "message": "Something went wrong",
        }

    def test_to_dict_with_subtype(self) -> None:
        event = ErrorEvent(
            message="Agent task timed out", subtype="session_timeout"
        )
        assert event.to_dict()["subtype"] == "session_timeout"

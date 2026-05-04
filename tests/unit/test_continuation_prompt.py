"""Unit tests for continuation prompt builder — controls prompt length and token consumption."""

from __future__ import annotations

import pytest

from main_server import _build_history_prompt


class TestBuildHistoryPromptBasic:
    def test_empty_history_produces_single_user_message(self) -> None:
        prompt = _build_history_prompt([], "Hello")
        assert "User: Hello" in prompt
        assert "Assistant:" in prompt

    def test_single_user_turn(self) -> None:
        history = [{"type": "user", "content": "What is 2+2?"}]
        prompt = _build_history_prompt(history, "And what is 3+3?")
        assert "User: What is 2+2?" in prompt
        assert "User: And what is 3+3?" in prompt

    def test_user_assistant_turn(self) -> None:
        history = [
            {"type": "user", "content": "What is 2+2?"},
            {"type": "assistant", "content": "It is 4."},
        ]
        prompt = _build_history_prompt(history, "Great!")
        assert "User: What is 2+2?" in prompt
        # Assistant messages are excluded to prevent Echo agents from repeating
        assert "Assistant: It is 4." not in prompt
        assert "User: Great!" in prompt

    def test_system_messages_are_skipped(self) -> None:
        history = [
            {"type": "system", "subtype": "progress", "data": {}},
            {"type": "system", "subtype": "session_state_changed", "state": "completed"},
        ]
        prompt = _build_history_prompt(history, "Continue")
        # Only system messages exist, so only the new user message should appear
        assert prompt.count("User:") == 1
        assert "User: Continue" in prompt

    def test_empty_content_messages_are_skipped(self) -> None:
        history = [
            {"type": "user", "content": ""},
            {"type": "assistant", "content": "   "},
        ]
        prompt = _build_history_prompt(history, "Hi")
        assert prompt.count("User:") == 1


class TestBuildHistoryPromptTruncation:
    def test_tool_result_is_truncated(self) -> None:
        long_content = "X" * 2000
        history = [{"type": "tool_result", "content": long_content}]
        prompt = _build_history_prompt(history, "Hi")
        # tool_result should be truncated to 200 chars
        assert "[Tool Result]" in prompt
        assert len(prompt) < 1000

    def test_tool_use_records_name_only(self) -> None:
        history = [
            {
                "type": "tool_use",
                "name": "Bash",
                "input": {"command": "ls -la /some/very/long/path/with/args"},
            },
        ]
        prompt = _build_history_prompt(history, "Hi")
        assert "[Tool: Bash]" in prompt
        # Full command should not appear (we record name only, not input)
        assert "ls -la" not in prompt


class TestBuildHistoryPromptWindowSize:
    def test_only_keeps_recent_messages(self) -> None:
        # Create 20 turns (user + assistant each)
        history = []
        for i in range(20):
            history.append({"type": "user", "content": f"Q{i}"})
            history.append({"type": "assistant", "content": f"A{i}"})

        prompt = _build_history_prompt(history, "Final question")

        # Assistant messages are excluded; only user messages are kept.
        # With max window, only the last ~10 user messages should be present.
        assert "Q0" not in prompt
        assert "A0" not in prompt
        assert "A19" not in prompt  # assistant messages always excluded
        # The last user turns should be present
        assert "Q19" in prompt

    def test_preserves_user_message_count_within_window(self) -> None:
        history = [
            {"type": "user", "content": f"msg-{i}"} for i in range(15)
        ]
        prompt = _build_history_prompt(history, "last")
        # With max 10 messages window, only the last 10 user messages should be present
        # Plus the new "last" message
        assert "msg-0" not in prompt
        assert "msg-4" not in prompt
        assert "msg-10" in prompt
        assert "msg-14" in prompt


class TestBuildHistoryPromptMaxLength:
    def test_total_length_does_not_exceed_max(self) -> None:
        # Create messages that would exceed the 8000 char limit
        history = [
            {"type": "user", "content": "X" * 3000},
            {"type": "user", "content": "Y" * 3000},
            {"type": "user", "content": "Z" * 3000},
        ]
        prompt = _build_history_prompt(history, "Hi")
        assert len(prompt) <= 8000
        # The final user message must always be present
        assert "User: Hi" in prompt


class TestBuildHistoryPromptLanguage:
    def test_english_mode_primes_assistant_turn(self) -> None:
        """When language=en, the assistant turn must be primed in English."""
        history = [{"type": "user", "content": "Hello"}]
        prompt = _build_history_prompt(history, "Tell me more", language="en")
        assert "Assistant (respond in English only):" in prompt

    def test_chinese_mode_primes_assistant_turn(self) -> None:
        """When language=zh, the assistant turn must be primed in Chinese."""
        history = [{"type": "user", "content": "你好"}]
        prompt = _build_history_prompt(history, "继续说", language="zh")
        assert "Assistant (respond in 中文 only):" in prompt

    def test_english_mode_flags_chinese_assistant_history(self) -> None:
        """When language=en, Chinese assistant responses must be flagged."""
        history = [
            {"type": "user", "content": "你好"},
            {"type": "assistant", "content": "你好！有什么可以帮助你的吗？"},
        ]
        prompt = _build_history_prompt(history, "Continue", language="en")
        assert "previous response was in Chinese" in prompt
        assert "IGNORE this language" in prompt

    def test_english_mode_does_not_flag_english_assistant_history(self) -> None:
        """When language=en, English assistant responses must NOT be flagged."""
        history = [
            {"type": "user", "content": "Hello"},
            {"type": "assistant", "content": "Hi there! How can I help?"},
        ]
        prompt = _build_history_prompt(history, "Continue", language="en")
        assert "previous response was in Chinese" not in prompt
        assert "Assistant: Hi there!" in prompt

    def test_chinese_mode_flags_english_assistant_history(self) -> None:
        """When language=zh, English assistant responses must be flagged."""
        history = [
            {"type": "user", "content": "Hello"},
            {"type": "assistant", "content": "Hello! How can I help you?"},
        ]
        prompt = _build_history_prompt(history, "继续", language="zh")
        assert "previous response was in English" in prompt

    def test_preamble_includes_language_directive(self) -> None:
        """Preamble must include language directive when language is set."""
        history: list[dict] = []
        prompt = _build_history_prompt(history, "Hello", language="en")
        assert "CRITICAL: You MUST respond in English" in prompt
        assert "Do not copy the language" in prompt

    def test_no_language_uses_neutral_assistant_prefix(self) -> None:
        """When language is not set, must use neutral 'Assistant:' prefix."""
        history = [{"type": "user", "content": "Hello"}]
        prompt = _build_history_prompt(history, "World")
        assert "Assistant:" in prompt
        assert "respond in" not in prompt

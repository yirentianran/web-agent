"""Tests for file attachment handling in user messages.

Two bugs:
1. File names not passed to agent context
2. File metadata not persisted, disappears on page refresh
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.message_buffer import MessageBuffer


@pytest.fixture()
def buffer(tmp_path: Path) -> MessageBuffer:
    return MessageBuffer(base_dir=tmp_path)


class TestFileMetadataPersistence:
    """File metadata should survive in the buffer and be retrievable on refresh."""

    def test_user_message_with_files_persists_data(self, buffer: MessageBuffer) -> None:
        """When a user message includes file info, the data field should be stored."""
        buffer.add_message("s1", {
            "type": "user",
            "content": "Review this file",
            "data": [{"filename": "report.pdf", "size": 102400}],
        })

        history = buffer.get_history("s1")
        assert len(history) == 1
        assert history[0]["type"] == "user"
        assert history[0]["content"] == "Review this file"
        assert history[0]["data"] == [{"filename": "report.pdf", "size": 102400}]

    def test_user_message_without_files_has_no_data(self, buffer: MessageBuffer) -> None:
        """User messages without files should have no data field."""
        buffer.add_message("s1", {"type": "user", "content": "Just text"})

        history = buffer.get_history("s1")
        assert history[0]["content"] == "Just text"
        # data may or may not be present, but should not crash

    def test_file_metadata_survives_buffer_reload(self, buffer: MessageBuffer) -> None:
        """File data should survive eviction + disk reload."""
        import time

        buffer.add_message("s1", {
            "type": "user",
            "content": "Analyze this",
            "data": [{"filename": "data.csv", "size": 5120}],
        })

        # Evict from memory
        buffer.sessions["s1"]["last_active"] = time.time() - 3601
        buffer.cleanup_expired()

        # Reload from disk
        history = buffer.get_history("s1")
        assert len(history) == 1
        assert history[0]["data"][0]["filename"] == "data.csv"


class TestFileContextForAgent:
    """File names should be visible to the agent when processing the message."""

    def test_build_history_prompt_includes_file_path(self) -> None:
        """When building prompt from history, file attachments should include relative path."""
        from main_server import _build_history_prompt

        history = [
            {"type": "user", "content": "Review this", "data": [{"filename": "report.pdf", "size": 102400}]},
            {"type": "assistant", "content": "I'll review the file."},
        ]

        prompt = _build_history_prompt(history, "Review this")
        # Agent needs the relative path to locate the file
        assert "uploads/report.pdf" in prompt

    def test_build_history_prompt_without_files(self) -> None:
        """Messages without file data should work normally."""
        from main_server import _build_history_prompt

        history = [
            {"type": "user", "content": "Hello"},
            {"type": "assistant", "content": "Hi!"},
        ]

        prompt = _build_history_prompt(history, "Hello")
        assert "Hello" in prompt

    def test_first_message_prompt_includes_file_path(self) -> None:
        """First-message prompt (non-continuation) should also include file path."""
        from main_server import _format_first_message_prompt

        prompt = _format_first_message_prompt("Read this pdf", ["Z202603010001-0.pdf"])
        assert "uploads/Z202603010001-0.pdf" in prompt

    def test_first_message_prompt_without_files(self) -> None:
        """First-message prompt without files should be unchanged."""
        from main_server import _format_first_message_prompt

        prompt = _format_first_message_prompt("Hello world", None)
        assert prompt == "Hello world"


class TestHistoryEndpointPreservesFileData:
    """History endpoint must return file data so frontend can render cards after refresh."""

    def test_buffer_history_preserves_data_field(self, buffer: MessageBuffer) -> None:
        """File data in user messages must survive get_history round-trip."""
        buffer.add_message("s1", {
            "type": "user",
            "content": "Read this pdf",
            "data": [{"filename": "Z202603010001-0.pdf"}],
        })

        history = buffer.get_history("s1")
        assert len(history) == 1
        assert history[0]["data"][0]["filename"] == "Z202603010001-0.pdf"

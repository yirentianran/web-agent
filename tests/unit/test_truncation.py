"""Unit tests for tool output truncation."""

from __future__ import annotations

from src.truncation import truncate_tool_output


class TestTruncateToolOutput:
    def test_short_output_not_truncated(self) -> None:
        result = truncate_tool_output("short output", max_chars=100)
        assert result == "short output"

    def test_exact_boundary_not_truncated(self) -> None:
        text = "x" * 100
        result = truncate_tool_output(text, max_chars=100)
        assert result == text

    def test_oversized_output_truncated(self) -> None:
        text = "A" * 5000
        result = truncate_tool_output(text, max_chars=1000)
        assert len(result) < len(text)
        assert "truncated" in result.lower()
        assert result.startswith("A" * 800)  # head preserved

    def test_summary_includes_stats(self) -> None:
        text = "line\n" * 1000  # 5000 chars, 1000 lines
        result = truncate_tool_output(text, max_chars=1000)
        assert "characters" in result
        assert "lines hidden" in result

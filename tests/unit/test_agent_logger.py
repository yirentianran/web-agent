"""Tests for L3 agent execution logger."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent_logger import AgentLogger


@pytest.fixture()
def tmp_agent_dir(tmp_path: Path) -> Path:
    return tmp_path / "agent-logs"


class TestAgentLogger:
    def test_start_session(self, tmp_agent_dir: Path) -> None:
        log = AgentLogger(user_id="alice", base_dir=tmp_agent_dir)
        log.start_session("sess_123", user_message="Hello")

        entries = log.query_session("sess_123")
        assert len(entries) == 1
        assert entries[0]["event"] == "session_start"
        assert entries[0]["user_message"] == "Hello"

    def test_tool_call_and_result(self, tmp_agent_dir: Path) -> None:
        log = AgentLogger(user_id="alice", base_dir=tmp_agent_dir)
        log.tool_call("Read", {"file_path": "test.py"}, session_id="sess_1")
        log.tool_result("Read", "file contents here", session_id="sess_1", duration_ms=42.5)

        entries = log.query_session("sess_1")
        assert len(entries) == 2
        assert entries[0]["tool"] == "Read"
        assert entries[0]["event"] == "tool_call"
        assert entries[1]["event"] == "tool_result"
        assert entries[1]["duration_ms"] == 42.5

    def test_end_session(self, tmp_agent_dir: Path) -> None:
        log = AgentLogger(user_id="alice", base_dir=tmp_agent_dir)
        log.end_session("sess_1", total_cost_usd=0.05, status="completed")

        entries = log.query_session("sess_1")
        assert len(entries) == 1
        assert entries[0]["event"] == "session_end"
        assert entries[0]["total_cost_usd"] == 0.05

    def test_tool_result_error(self, tmp_agent_dir: Path) -> None:
        log = AgentLogger(user_id="bob", base_dir=tmp_agent_dir)
        log.tool_result("Bash", "", session_id="sess_2", error="command failed", duration_ms=100)

        entries = log.query_session("sess_2")
        assert entries[0]["error"] == "command failed"

    def test_long_output_truncated(self, tmp_agent_dir: Path) -> None:
        log = AgentLogger(user_id="alice", base_dir=tmp_agent_dir)
        long_output = "x" * 20000
        log.tool_result("Bash", long_output, session_id="sess_3")

        entries = log.query_session("sess_3")
        assert len(entries[0]["output"]) < len(long_output)
        assert "truncated" in entries[0]["output"]

    def test_query_nonexistent_session(self, tmp_agent_dir: Path) -> None:
        log = AgentLogger(user_id="alice", base_dir=tmp_agent_dir)
        assert log.query_session("nonexistent") == []

    def test_turn_number(self, tmp_agent_dir: Path) -> None:
        log = AgentLogger(user_id="alice", base_dir=tmp_agent_dir)
        log.tool_call("Read", {"file_path": "a.py"}, session_id="sess_4", turn=3)
        log.tool_result("Read", "ok", session_id="sess_4", turn=3)

        entries = log.query_session("sess_4")
        assert entries[0]["turn"] == 3
        assert entries[1]["turn"] == 3

    def test_full_session_lifecycle(self, tmp_agent_dir: Path) -> None:
        log = AgentLogger(user_id="alice", base_dir=tmp_agent_dir)
        log.start_session("sess_5", "Analyze report")
        log.tool_call("Read", {"file_path": "report.md"}, session_id="sess_5", turn=1)
        log.tool_result("Read", "report content", session_id="sess_5", duration_ms=50, turn=1)
        log.end_session("sess_5", total_cost_usd=0.12)

        entries = log.query_session("sess_5")
        assert len(entries) == 4
        assert [e["event"] for e in entries] == [
            "session_start", "tool_call", "tool_result", "session_end",
        ]

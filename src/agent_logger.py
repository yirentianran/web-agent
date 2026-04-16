"""L3 Agent Execution Logger — tool_use chains with timing.

Records tool_use input/output with duration, token usage, and session context.
Per-user, per-session JSONL files for easy inspection.

Usage:
    from src.agent_logger import AgentLogger

    log = AgentLogger(user_id="alice")
    log.start_session("session_abc123")
    log.tool_call("Read", {"file_path": "report.md"}, session_id="session_abc123")
    log.tool_result("Read", "Content here...", duration_ms=45, session_id="session_abc123")
    log.end_session(session_id="session_abc123", total_cost_usd=0.15)
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AGENT_LOG_DIR = Path(os.getenv("AGENT_LOG_DIR", "/data/logs/agent"))
MAX_TOOL_OUTPUT_CHARS = int(os.getenv("MAX_TOOL_OUTPUT_CHARS", "8000"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(value: str, max_chars: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    """Truncate long tool output for logging."""
    if len(value) <= max_chars:
        return value
    return value[:max_chars - 100] + f"\n... [truncated {len(value) - max_chars + 100} chars]"


class AgentLogger:
    """Per-user agent execution logger with timing."""

    def __init__(self, user_id: str, base_dir: Path = AGENT_LOG_DIR) -> None:
        self.user_id = user_id
        self.base_dir = base_dir / user_id
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _session_file(self, session_id: str) -> Path:
        return self.base_dir / f"{session_id}.jsonl"

    def start_session(self, session_id: str, user_message: str | None = None) -> None:
        """Log session start."""
        entry = {
            "event": "session_start",
            "timestamp": _now_iso(),
            "session_id": session_id,
        }
        if user_message:
            entry["user_message"] = user_message[:200]
        self._append(session_id, entry)

    def tool_call(
        self,
        tool_name: str,
        tool_input: Any,
        session_id: str,
        turn: int | None = None,
    ) -> None:
        """Log a tool_use event."""
        entry = {
            "event": "tool_call",
            "timestamp": _now_iso(),
            "session_id": session_id,
            "tool": tool_name,
            "input": tool_input,
        }
        if turn is not None:
            entry["turn"] = turn
        self._append(session_id, entry)

    def tool_result(
        self,
        tool_name: str,
        output: str,
        session_id: str,
        duration_ms: float | None = None,
        error: str | None = None,
        turn: int | None = None,
    ) -> None:
        """Log a tool_result event."""
        entry = {
            "event": "tool_result",
            "timestamp": _now_iso(),
            "session_id": session_id,
            "tool": tool_name,
            "output": _truncate(output),
        }
        if duration_ms is not None:
            entry["duration_ms"] = round(duration_ms, 2)
        if error:
            entry["error"] = error[:500]
        if turn is not None:
            entry["turn"] = turn
        self._append(session_id, entry)

    def end_session(
        self,
        session_id: str,
        total_cost_usd: float | None = None,
        status: str = "completed",
    ) -> None:
        """Log session end."""
        entry = {
            "event": "session_end",
            "timestamp": _now_iso(),
            "session_id": session_id,
            "status": status,
        }
        if total_cost_usd is not None:
            entry["total_cost_usd"] = round(total_cost_usd, 6)
        self._append(session_id, entry)

    def query_session(self, session_id: str) -> list[dict[str, Any]]:
        """Read all log entries for a session."""
        log_file = self._session_file(session_id)
        if not log_file.exists():
            return []
        entries: list[dict[str, Any]] = []
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return entries

    def _append(self, session_id: str, entry: dict[str, Any]) -> None:
        log_file = self._session_file(session_id)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

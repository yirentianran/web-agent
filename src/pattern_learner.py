"""Pattern learner — extracts implicit signals from conversation data."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.database import Database

logger = logging.getLogger(__name__)


class PatternLearner:
    """Extracts tool usage patterns and implicit feedback signals."""

    def __init__(self, db: "Database") -> None:
        self.db = db

    async def extract_tool_patterns(self) -> dict[str, Any]:
        """Analyze messages table for tool co-occurrence and success rates.

        Returns dict with tool_pairs, tool_success_rates, and tool_error_rates.
        """
        async with self.db.connection() as conn:
            # Extract tool usage by session
            cursor = await conn.execute(
                """SELECT session_id, payload
                   FROM messages
                   WHERE type = 'tool_use'
                   AND created_at > strftime('%s', 'now') - 86400
                   ORDER BY session_id, created_at"""
            )
            rows = await cursor.fetchall()

        # Group tools by session
        session_tools: dict[str, list[str]] = {}
        tool_counts: dict[str, int] = {}

        for row in rows:
            session_id = row[0]
            try:
                payload = json.loads(row[1]) if row[1] else {}
                tool_name = payload.get("name", "unknown")
            except (json.JSONDecodeError, TypeError):
                tool_name = "unknown"

            session_tools.setdefault(session_id, []).append(tool_name)
            tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1

        # Calculate co-occurrence
        pair_counts: dict[tuple[str, str], int] = {}
        for tools in session_tools.values():
            unique_tools = list(set(tools))
            for i, t1 in enumerate(unique_tools):
                for t2 in unique_tools[i + 1:]:
                    pair = tuple(sorted([t1, t2]))
                    pair_counts[pair] = pair_counts.get(pair, 0) + 1

        # Top co-occurring pairs
        top_pairs = sorted(pair_counts.items(), key=lambda x: x[1], reverse=True)[:20]

        # Calculate actual success/failure rates
        success_rates = await self._calculate_tool_success_rates()

        result = {
            "tool_pairs": [
                {
                    "tools": list(pair),
                    "co_occurrence": count,
                }
                for pair, count in top_pairs
            ],
            "tool_success_rates": success_rates,
        }

        # Persist to DB
        async with self.db.connection() as conn:
            await conn.execute(
                """INSERT INTO learned_patterns (pattern_type, pattern_data, confidence)
                   VALUES (?, ?, ?)""",
                ("tool_cooccurrence", json.dumps(result), 0.8),
            )
            await conn.commit()

        return result

    async def _calculate_tool_success_rates(self) -> dict[str, dict[str, int]]:
        """Calculate actual success/failure counts per tool by correlating
        tool_use and tool_result messages within the last 24 hours."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT session_id, type, payload
                   FROM messages
                   WHERE (type = 'tool_use' OR type = 'tool_result')
                   AND created_at > strftime('%s', 'now') - 86400
                   ORDER BY session_id, created_at"""
            )
            rows = await cursor.fetchall()

        # Build tool_use -> tool_result mapping per session
        # tool_result messages contain 'tool_use_id' that references the tool_use id
        session_uses: dict[str, list[dict]] = {}
        session_results: dict[str, list[dict]] = {}

        for session_id, msg_type, payload_str in rows:
            try:
                payload = json.loads(payload_str) if payload_str else {}
            except (json.JSONDecodeError, TypeError):
                payload = {}

            if msg_type == "tool_use":
                tool_id = payload.get("id", "")
                tool_name = payload.get("name", "unknown")
                session_uses.setdefault(session_id, []).append({
                    "id": tool_id,
                    "name": tool_name,
                })
            elif msg_type == "tool_result":
                tool_use_id = payload.get("tool_use_id", "")
                is_error = payload.get("is_error", False)
                session_results.setdefault(session_id, []).append({
                    "tool_use_id": tool_use_id,
                    "is_error": bool(is_error),
                })

        # Match uses with results
        tool_success: dict[str, int] = {}
        tool_failure: dict[str, int] = {}

        for session_id, uses in session_uses.items():
            results = {r["tool_use_id"]: r["is_error"] for r in session_results.get(session_id, [])}
            for use in uses:
                name = use["name"]
                tool_id = use["id"]
                is_error = results.get(tool_id, False)
                if is_error:
                    tool_failure[name] = tool_failure.get(name, 0) + 1
                else:
                    tool_success[name] = tool_success.get(name, 0) + 1

        # Build result with total, success, failure, and rate
        all_tools = set(list(tool_success.keys()) + list(tool_failure.keys()))
        result = {}
        for name in sorted(all_tools, key=lambda n: tool_success.get(n, 0) + tool_failure.get(n, 0), reverse=True):
            s = tool_success.get(name, 0)
            f = tool_failure.get(name, 0)
            total = s + f
            rate = round(s / total * 100, 1) if total > 0 else 0
            result[name] = {
                "total": total,
                "success": s,
                "failure": f,
                "rate": rate,
            }

        return result

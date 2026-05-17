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

        Returns dict with tool_pairs and tool_success_rates.
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

        result = {
            "tool_pairs": [
                {
                    "tools": list(pair),
                    "co_occurrence": count,
                }
                for pair, count in top_pairs
            ],
            "tool_success_rates": {
                name: {"count": cnt}
                for name, cnt in sorted(
                    tool_counts.items(), key=lambda x: x[1], reverse=True
                )[:20]
            },
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

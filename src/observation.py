"""Event capture for instinct evolution. Writes structured observations to SQLite."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class ObservationStore:
    """Write and query tool-call and user-interaction events."""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def record(
        self,
        *,
        session_id: str,
        user_id: str,
        event_type: str,
        tool_name: str = "",
        tool_input_summary: str = "",
        tool_output_summary: str = "",
        success: bool | None = None,
        error_message: str = "",
        duration_ms: int = 0,
    ) -> int:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """INSERT INTO observations
                   (session_id, user_id, event_type, tool_name,
                    tool_input_summary, tool_output_summary,
                    success, error_message, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id, user_id, event_type, tool_name,
                    tool_input_summary[:500], tool_output_summary[:500],
                    1 if success else 0 if success is not None else None,
                    error_message[:500], duration_ms,
                ),
            )
            return cursor.lastrowid

    async def count_since(self, since_timestamp: float) -> int:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM observations WHERE created_at > ?",
                (since_timestamp,),
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def get_new_since(
        self, since_timestamp: float, limit: int = 500
    ) -> list[dict[str, Any]]:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT id, session_id, user_id, event_type, tool_name,
                          tool_input_summary, tool_output_summary,
                          success, error_message, duration_ms, created_at
                   FROM observations
                   WHERE created_at > ?
                   ORDER BY created_at ASC
                   LIMIT ?""",
                (since_timestamp, limit),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "id": r[0], "session_id": r[1], "user_id": r[2],
                    "event_type": r[3], "tool_name": r[4],
                    "tool_input_summary": r[5], "tool_output_summary": r[6],
                    "success": bool(r[7]) if r[7] is not None else None,
                    "error_message": r[8], "duration_ms": r[9], "created_at": r[10],
                }
                for r in rows
            ]

    async def list_events(
        self,
        *,
        session_id: str = "",
        event_type: str = "",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        conditions = []
        params: list[Any] = []
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        async with self.db.connection() as conn:
            count_row = await conn.execute_fetchall(
                f"SELECT COUNT(*) FROM observations {where}", params
            )
            total = count_row[0][0] if count_row else 0

            offset = (page - 1) * page_size
            cursor = await conn.execute(
                f"""SELECT id, session_id, user_id, event_type, tool_name,
                           success, error_message, duration_ms, created_at
                    FROM observations {where}
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?""",
                params + [page_size, offset],
            )
            rows = await cursor.fetchall()

        return {
            "items": [
                {
                    "id": r[0], "session_id": r[1], "user_id": r[2],
                    "event_type": r[3], "tool_name": r[4],
                    "success": bool(r[5]) if r[5] is not None else None,
                    "error_message": r[6], "duration_ms": r[7], "created_at": r[8],
                }
                for r in rows
            ],
            "total": total,
            "page": page,
        }

    async def get_stats(self) -> dict[str, Any]:
        """Return dashboard stats: today's events, by-type breakdown."""
        now = time.time()
        today_start = now - (now % 86400)
        week_start = now - 7 * 86400

        async with self.db.connection() as conn:
            today_total = await conn.execute_fetchall(
                "SELECT COUNT(*) FROM observations WHERE created_at >= ?",
                (today_start,),
            )
            week_auto = await conn.execute_fetchall(
                """SELECT COUNT(*) FROM observations
                   WHERE created_at >= ? AND event_type = 'session_complete'""",
                (week_start,),
            )
        return {
            "today_events": today_total[0][0] if today_total else 0,
            "week_completions": week_auto[0][0] if week_auto else 0,
        }

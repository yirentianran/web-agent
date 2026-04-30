"""L1 Audit Logger — SQL-based, append-only audit trail.

Usage:
    from src.audit_logger import AuditLogger

    audit = AuditLogger(db=database)
    await audit.log("auth", {"user_id": "alice", "action": "login", "result": "ok"})
    entries = await audit.query("auth", user_id="alice")
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.database import Database

_CATEGORIES = {"auth", "skills", "mcp", "files", "admin", "session", "resource"}


class AuditLogger:
    """SQL-based append-only audit log."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def log(self, category: str, data: dict[str, Any]) -> None:
        """Append an audit log entry. Raises ValueError for invalid category."""
        if category not in _CATEGORIES:
            raise ValueError(
                f"Invalid audit category: {category}. Must be one of {sorted(_CATEGORIES)}"
            )

        user_id = data.get("user_id")
        action = data.get("action")
        async with self.db.connection() as conn:
            await conn.execute(
                "INSERT INTO audit_log (category, user_id, action, data, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (category, user_id, action, _safe_dumps(data), time.time()),
            )
            await conn.commit()

    async def query(
        self,
        category: str,
        *,
        user_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query audit log entries. Returns matching entries, newest first."""
        if category not in _CATEGORIES:
            raise ValueError(
                f"Invalid audit category: {category}. Must be one of {sorted(_CATEGORIES)}"
            )

        import json

        conditions = ["category = ?"]
        params: list[Any] = [category]

        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        if action is not None:
            conditions.append("action = ?")
            params.append(action)

        where = " AND ".join(conditions)
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT id, category, user_id, action, data, created_at "
                f"FROM audit_log WHERE {where} ORDER BY created_at DESC LIMIT ?",
                (*params, limit),
            )
            rows = await cursor.fetchall()

        return [
            {
                "id": row[0],
                "category": row[1],
                "user_id": row[2],
                "action": row[3],
                "data": json.loads(row[4]) if isinstance(row[4], str) else row[4],
                "created_at": row[5],
            }
            for row in rows
        ]


def _safe_dumps(data: dict[str, Any]) -> str:
    import json
    return json.dumps(data, ensure_ascii=False, default=str)

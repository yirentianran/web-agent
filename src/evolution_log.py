"""CRUD for evolution_log table."""
from __future__ import annotations

import time
from typing import Any

if __name__ != "__main__":
    from src.database import Database


class EvolutionLogStore:
    """CRUD for evolution tracking tables."""

    def __init__(self, db: "Database") -> None:
        self.db = db

    async def create_log(
        self,
        skill_name: str,
        from_version: str,
        to_version: str,
        *,
        source: str = "session_learner",
        evolve_reason: str = "",
        proposed_content: str = "",
        baseline_composite: float | None = None,
        baseline_metrics: str = "",
        status: str = "active",
    ) -> dict[str, Any]:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """INSERT INTO evolution_log
                   (skill_name, from_version, to_version, source, evolve_reason,
                    proposed_content, baseline_composite, baseline_metrics, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (skill_name, from_version, to_version, source, evolve_reason,
                 proposed_content, baseline_composite, baseline_metrics, status,
                 int(time.time())),
            )
            return {"id": cursor.lastrowid}

    async def update_status(
        self, log_id: int, status: str, **extra: Any
    ) -> None:
        sets = ["status = ?"]
        params: list[Any] = [status]
        for key, val in extra.items():
            sets.append(f"{key} = ?")
            params.append(val)
        params.append(log_id)
        async with self.db.connection() as conn:
            await conn.execute(
                f"UPDATE evolution_log SET {', '.join(sets)} WHERE id = ?",
                params,
            )

    async def get_log(self, log_id: int) -> dict[str, Any] | None:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM evolution_log WHERE id = ?", (log_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_log_with_instincts(self, log_id: int) -> dict[str, Any] | None:
        """Get evolution log with linked instincts."""
        log = await self.get_log(log_id)
        if not log:
            return None
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT id, domain, normalized_trigger, trigger, action, confidence
                   FROM instincts WHERE source_evolution_id = ?""",
                (log_id,),
            )
            rows = await cursor.fetchall()
        log["instincts"] = [
            {
                "id": r[0], "domain": r[1], "normalized_trigger": r[2],
                "trigger": r[3], "action": r[4], "confidence": r[5],
            }
            for r in rows
        ]
        return log

    async def list_logs(
        self,
        *,
        status: str | None = None,
        skill_name: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        where = []
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if skill_name:
            where.append("skill_name = ?")
            params.append(skill_name)

        clause = f"WHERE {' AND '.join(where)}" if where else ""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT COUNT(*) FROM evolution_log {clause}", params
            )
            row = await cursor.fetchone()
            total = row[0] if row else 0

            offset = (page - 1) * page_size
            cursor = await conn.execute(
                f"""SELECT * FROM evolution_log {clause}
                    ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                params + [page_size, offset],
            )
            rows = await cursor.fetchall()
            return {
                "items": [dict(r) for r in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
            }

    async def get_proposed(self) -> list[dict[str, Any]]:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT * FROM evolution_log
                   WHERE status = 'proposed'
                   ORDER BY created_at DESC""",
            )
            return [dict(r) for r in await cursor.fetchall()]

    async def get_overview_stats(self, days: int = 0) -> dict[str, Any]:
        """Dashboard stats. days=0 means today only, days>0 means last N days."""
        if days > 0:
            cutoff = time.time() - days * 86400
        else:
            cutoff = time.time() - (time.time() % 86400)
        week_cutoff = time.time() - 7 * 86400

        async with self.db.connection() as conn:
            status_rows = await conn.execute_fetchall(
                "SELECT status, COUNT(*) FROM evolution_log GROUP BY status"
            )
            status_counts = {r[0]: r[1] for r in status_rows}

            instinct_active_row = await conn.execute_fetchall(
                "SELECT COUNT(*) FROM instincts WHERE scope = 'active'"
            )
            instinct_active = instinct_active_row[0][0] if instinct_active_row else 0

            obs_count = await conn.execute_fetchall(
                "SELECT COUNT(*) FROM observations WHERE created_at >= ?", (cutoff,),
            )
            events_in_window = obs_count[0][0] if obs_count else 0

            evo_active = await conn.execute_fetchall(
                "SELECT COUNT(*) FROM evolution_log WHERE status = 'active' AND created_at >= ?",
                (cutoff,),
            )
            evo_active_in_window = evo_active[0][0] if evo_active else 0

            evo_proposed = await conn.execute_fetchall(
                "SELECT COUNT(*) FROM evolution_log WHERE status = 'proposed' AND created_at >= ?",
                (cutoff,),
            )
            evo_proposed_in_window = evo_proposed[0][0] if evo_proposed else 0

            week_applied = await conn.execute_fetchall(
                """SELECT COUNT(*) FROM evolution_log
                   WHERE status = 'active' AND source = 'instinct_extractor'
                   AND created_at >= ?""",
                (week_cutoff,),
            )
            week_auto = week_applied[0][0] if week_applied else 0

            if days == 0:
                today_events = events_in_window
            else:
                today_start = time.time() - (time.time() % 86400)
                today_row = await conn.execute_fetchall(
                    "SELECT COUNT(*) FROM observations WHERE created_at >= ?",
                    (today_start,),
                )
                today_events = today_row[0][0] if today_row else 0

        return {
            "today_events": today_events,
            "active_instincts": instinct_active,
            "pending_reviews": status_counts.get("proposed", 0),
            "week_auto_applied": week_auto,
            "funnel": {
                "observations": events_in_window,
                "active_instincts": instinct_active,
                "active_evolutions": evo_active_in_window,
                "proposed_evolutions": evo_proposed_in_window,
            },
            "time_window": f"last_{days}_days" if days > 0 else "today",
        }

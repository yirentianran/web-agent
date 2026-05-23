"""Daily evaluation of evolution quality using observation-derived signals."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class EvolutionSignals:
    """Tracks skill success rate and failure trends. Triggers rollback on degradation."""

    def __init__(self, db: Any, evolution_store: Any, skill_manager: Any) -> None:
        self.db = db
        self.evolution_store = evolution_store
        self.skill_manager = skill_manager

    async def run_daily_eval(self) -> dict[str, Any]:
        """Run daily evaluation for all active evolutions."""
        active = await self.evolution_store.get_active_evolutions()
        result = {"evaluated": 0, "degraded": 0, "rolled_back": 0}

        yesterday = time.strftime(
            "%Y-%m-%d", time.localtime(time.time() - 86400)
        )

        for log in active:
            snapshot = await self._compute_daily_snapshot(log, yesterday)
            if snapshot:
                await self.evolution_store.create_snapshot(**snapshot)
                result["evaluated"] += 1

            # Check last 7 snapshots for degradation
            recent = await self.evolution_store.get_last_snapshots(log["id"], count=7)
            if len(recent) >= 7:
                baseline = log.get("baseline_composite") or self._compute_baseline(log)
                all_below = all(
                    (s.get("composite_score") or 0) < baseline for s in recent
                )
                if all_below:
                    await self.evolution_store.update_status(
                        log["id"],
                        "under_review",
                        auto_rollback_at=time.time() + 48 * 3600,
                    )
                    result["degraded"] += 1

        # Process expired reviews
        expired = await self.evolution_store.get_expired_reviews()
        for log in expired:
            await self._rollback(log)
            result["rolled_back"] += 1

        return result

    async def _compute_daily_snapshot(
        self, log: dict[str, Any], date_str: str
    ) -> dict[str, Any] | None:
        """Compute one day's snapshot using observation data."""
        skill_name = log["skill_name"]
        date_start = date_str
        date_end = date_str + "T23:59:59"

        async with self.db.connection() as conn:
            # Tool success rate
            rows = await conn.execute_fetchall(
                """SELECT success, COUNT(*) as cnt FROM observations
                   WHERE created_at >= ? AND created_at <= ?
                   AND event_type = 'tool_call_end'
                   AND success IS NOT NULL
                   GROUP BY success""",
                (date_start, date_end),
            )
            total = sum(r[1] for r in rows)
            success_count = sum(r[1] for r in rows if r[0] == 1)
            tool_success_rate = success_count / total if total > 0 else 1.0

            # Session completion rate
            session_rows = await conn.execute_fetchall(
                """SELECT event_type, COUNT(DISTINCT session_id) as cnt
                   FROM observations
                   WHERE created_at >= ? AND created_at <= ?
                   AND event_type IN ('session_complete', 'session_error')
                   GROUP BY event_type""",
                (date_start, date_end),
            )
            sc = {r[0]: r[1] for r in session_rows}
            completed = sc.get("session_complete", 0)
            errored = sc.get("session_error", 0)
            session_rate = completed / (completed + errored) if (completed + errored) > 0 else 1.0

            # Usage count
            usage = total

            # Unique users
            user_rows = await conn.execute_fetchall(
                """SELECT COUNT(DISTINCT user_id) FROM observations
                   WHERE created_at >= ? AND created_at <= ?""",
                (date_start, date_end),
            )
            unique_users = user_rows[0][0] if user_rows else 0

            # Composite score
            composite = 0.5 * tool_success_rate + 0.3 * session_rate + 0.2 * min(1.0, usage / 50)

            return {
                "evolution_log_id": log["id"],
                "snapshot_date": date_str,
                "usage_count": usage,
                "unique_users": unique_users,
                "avg_rating": 0,
                "session_success_rate": session_rate,
                "composite_score": round(composite, 4),
            }

    def _compute_baseline(self, log: dict[str, Any]) -> float:
        return log.get("baseline_composite") or 0.6

    async def _rollback(self, log: dict[str, Any]) -> None:
        """Restore previous version of the skill."""
        try:
            skill_name = log["skill_name"]
            await self.skill_manager.rollback_version(skill_name)
            await self.evolution_store.update_status(log["id"], "rolled_back")
            logger.info("Rolled back %s (evolution #%d)", skill_name, log["id"])
        except Exception as exc:
            logger.error("Rollback failed for evolution #%d: %s", log["id"], exc)

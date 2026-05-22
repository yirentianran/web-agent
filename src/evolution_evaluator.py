"""Daily evolution evaluation: snapshot generation and degradation detection."""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

if __name__ != "__main__":
    from src.database import Database
    from src.evolution_log import EvolutionLogStore

logger = logging.getLogger(__name__)

# Weights for composite score
W_RATING = 0.4
W_USAGE = 0.3
W_SUCCESS = 0.3


class EvolutionEvaluator:
    """Generates daily snapshots and detects degradation."""

    def __init__(self, db: "Database") -> None:
        self.db = db
        self.store = EvolutionLogStore(db)

    async def run_daily_eval(self) -> None:
        """Run the daily evaluation cycle (called by CI scheduler at 02:00).

        Snapshots yesterday's full day of data (since the scheduler fires
        at 02:00 today, yesterday is the most recent complete day).
        """
        active = await self.store.get_active_evolutions()
        yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")

        for log in active:
            snap = await self._compute_snapshot(log, yesterday)
            await self.store.create_snapshot(**snap)

            last_7 = await self.store.get_last_snapshots(log["id"], 7)
            if len(last_7) < 7:
                continue

            # Use baseline_composite stored at evolution creation time
            baseline = log.get("baseline_composite")
            if baseline is None:
                baseline = 0.6
            if all(s["composite_score"] < baseline for s in last_7):
                rollback_at = int(time.time()) + 48 * 3600
                await self.store.update_status(
                    log["id"], "under_review", auto_rollback_at=rollback_at
                )
                logger.warning(
                    "Skill %s (log %d) degraded — under_review, auto-rollback at %d",
                    log["skill_name"],
                    log["id"],
                    rollback_at,
                )

    async def _compute_snapshot(
        self, log: dict, date_str: str
    ) -> dict:
        """Compute a single day's snapshot for an evolution."""
        skill_name = log["skill_name"]
        date_start = f"{date_str}T00:00:00"
        date_end = f"{date_str}T23:59:59"

        async with self.db.connection() as conn:
            # Usage count for the snapshot date
            cursor = await conn.execute(
                """SELECT COUNT(*) FROM skill_usage
                   WHERE skill_name = ? AND created_at >= strftime('%s', ?)
                   AND created_at <= strftime('%s', ?)""",
                (skill_name, date_start, date_end),
            )
            row = await cursor.fetchone()
            usage_count = row[0] if row else 0

            # Daily unique users
            cursor = await conn.execute(
                """SELECT COUNT(DISTINCT user_id) FROM skill_usage
                   WHERE skill_name = ?
                   AND created_at >= strftime('%s', ?)
                   AND created_at <= strftime('%s', ?)""",
                (skill_name, date_start, date_end),
            )
            row = await cursor.fetchone()
            unique_users = row[0] if row else 0

            # Avg rating
            cursor = await conn.execute(
                "SELECT AVG(rating) FROM skill_feedback WHERE skill_name = ?",
                (skill_name,),
            )
            row = await cursor.fetchone()
            avg_rating = row[0] if row and row[0] else 0.0

            # Session success rate: completed / total for sessions using this skill
            cursor = await conn.execute(
                """SELECT
                    CAST(SUM(CASE WHEN s.status = 'completed' THEN 1 ELSE 0 END) AS REAL) / COUNT(*)
                   FROM sessions s
                   WHERE s.session_id IN (
                       SELECT DISTINCT session_id FROM skill_usage WHERE skill_name = ?
                   )""",
                (skill_name,),
            )
            row = await cursor.fetchone()
            session_success_rate = row[0] if row and row[0] else 0.0

        # Baseline daily usage from 7 days before evolution
        evolved_at = log["created_at"]
        baseline_period_start = evolved_at - 7 * 86400
        baseline_daily = 5  # default
        try:
            async with self.db.connection() as conn:
                cursor = await conn.execute(
                    """SELECT COUNT(*) / 7.0 FROM skill_usage
                       WHERE skill_name = ? AND created_at BETWEEN ? AND ?""",
                    (skill_name, baseline_period_start, evolved_at),
                )
                row = await cursor.fetchone()
                if row and row[0]:
                    baseline_daily = max(row[0], 1)
        except Exception:
            pass

        # Usage trend ratio
        days_since_evo = max((int(time.time()) - evolved_at) / 86400, 1)
        current_daily = usage_count / days_since_evo if usage_count > 0 else 0
        usage_trend_ratio = min(current_daily / baseline_daily, 1.0)

        composite = (
            W_RATING * (avg_rating / 5.0)
            + W_USAGE * usage_trend_ratio
            + W_SUCCESS * session_success_rate
        )

        return {
            "evolution_log_id": log["id"],
            "snapshot_date": date_str,
            "usage_count": usage_count,
            "unique_users": unique_users,
            "avg_rating": round(avg_rating, 2),
            "session_success_rate": round(session_success_rate, 2),
            "composite_score": round(composite, 4),
        }

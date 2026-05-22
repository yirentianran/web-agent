"""Rollback state machine: restore previous skill version on degradation."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

if __name__ != "__main__":
    from src.database import Database
    from src.evolution_log import EvolutionLogStore

logger = logging.getLogger(__name__)


class EvolutionRollback:
    """Executes skill version rollback when evolution degrades.

    Uses SkillManager.rollback_version() (src/skill_manager.py:382), not
    DBSkillFeedbackManager.rollback_version() — SkillManager resolves paths
    from the DB and handles both shared/personal skills without a skills_dir param.
    """

    def __init__(
        self,
        db: "Database",
        data_root: Path,
        skill_manager: Any = None,
        on_skill_changed: "Callable[[], None] | None" = None,
    ) -> None:
        self.db = db
        self.data_root = data_root
        self.skill_manager = skill_manager
        self.on_skill_changed = on_skill_changed
        self.store = EvolutionLogStore(db)

    async def execute_rollback(self, log_id: int, reason: str = "auto-rollback") -> bool:
        """Roll back a skill to its previous version via SkillManager.

        Returns True on success, False if rollback not possible.
        """
        log = await self.store.get_log(log_id)
        if not log:
            logger.error("Rollback failed: evolution_log %d not found", log_id)
            return False

        skill_name = log["skill_name"]

        # Delegate to SkillManager.rollback_version() for proper version restore
        if self.skill_manager is not None:
            try:
                await self.skill_manager.rollback_version(skill_name)
            except Exception as e:
                logger.error("SkillManager rollback failed for %s: %s", skill_name, e)
                return False
        else:
            logger.error("Rollback failed: no SkillManager available")
            return False

        # Update evolution_log
        await self.store.update_status(
            log_id,
            "rolled_back",
            reviewed_at=int(time.time()),
            review_decision="rolled_back",
        )

        # Bump shared skills generation so user workspaces re-sync
        if self.on_skill_changed:
            self.on_skill_changed()

        logger.info("Rolled back %s (log %d, reason: %s)", skill_name, log_id, reason)
        return True

    async def process_expired_reviews(self) -> int:
        """Auto-rollback all under_review evolutions past their 48h deadline.

        Returns count of rollbacks executed.
        """
        expired = await self.store.get_expired_reviews()
        count = 0
        for log in expired:
            success = await self.execute_rollback(log["id"], reason="48h auto-rollback")
            if success:
                count += 1
        return count

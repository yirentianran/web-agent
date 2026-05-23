"""Collective intelligence engine — coordinates all background intelligence jobs."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from src.evolution_evaluator import EvolutionEvaluator
from src.evolution_rollback import EvolutionRollback
from src.pattern_learner import PatternLearner
from src.semantic_search import SemanticSearch
from src.wiki_generator import WikiGenerator

if __name__ != "__main__":
    from src.database import Database
    from src.skill_manager import SkillManager

logger = logging.getLogger(__name__)


class CollectiveIntelligenceEngine:
    """Orchestrates L3/L4/L5 background intelligence tasks."""

    def __init__(self, db: "Database", data_root: Path) -> None:
        self.db = db
        self.data_root = data_root
        self.wiki_generator = WikiGenerator(db)
        self.semantic_search = SemanticSearch(db)
        self.pattern_learner = PatternLearner(db)
        self.skill_manager = SkillManager(db)

    async def start_background_jobs(self) -> None:
        """Launch all background intelligence loops."""
        asyncio.create_task(self._wiki_mining_loop())
        asyncio.create_task(self._pattern_extraction_loop())
        asyncio.create_task(self._auto_promotion_loop())
        asyncio.create_task(self._eval_snapshot_loop())
        logger.info("Collective intelligence background jobs started")

    async def _wiki_mining_loop(self) -> None:
        while True:
            try:
                generated = await self.wiki_generator.mine_and_generate(lookback_hours=6)
                if generated:
                    logger.info(f"Wiki generated {len(generated)} new pages: {generated}")
            except Exception:
                logger.exception("Wiki mining loop failed")
            await asyncio.sleep(6 * 3600)

    async def _pattern_extraction_loop(self) -> None:
        while True:
            try:
                result = await self.pattern_learner.extract_tool_patterns()
                logger.info(
                    f"Pattern extraction: {len(result.get('tool_pairs', []))} pairs found"
                )
            except Exception:
                logger.exception("Pattern extraction loop failed")
            await asyncio.sleep(12 * 3600)

    async def _auto_promotion_loop(self) -> None:
        while True:
            try:
                candidates = await self.skill_manager.check_auto_promotion()
                if candidates:
                    logger.info(
                        f"Auto-promotion candidates: "
                        f"{[c['skill_name'] for c in candidates]}"
                    )

                # Clean up expired promotions
                expired = await self.skill_manager.cleanup_expired_promotions()
                if expired:
                    logger.info(f"Auto-rejected {expired} expired promotions")
            except Exception:
                logger.exception("Auto-promotion check failed")
            await asyncio.sleep(2 * 3600)

    async def _eval_snapshot_loop(self) -> None:
        """Daily evaluation: snapshot active evolutions, detect degradation, auto-rollback."""
        evaluator = EvolutionEvaluator(self.db)
        rollback = EvolutionRollback(self.db, self.data_root)

        while True:
            try:
                # Wait until 02:00 local time, then run once per day
                now = datetime.now()
                next_run = now.replace(hour=2, minute=0, second=0, microsecond=0)
                if now >= next_run:
                    next_run += timedelta(days=1)
                wait_seconds = (next_run - now).total_seconds()
                await asyncio.sleep(wait_seconds)

                await evaluator.run_daily_eval()
                count = await rollback.process_expired_reviews()
                if count:
                    logger.info("Auto-rolled back %d degraded evolutions", count)
            except Exception:
                logger.exception("Eval snapshot loop failed")
                await asyncio.sleep(3600)  # retry after 1h on error

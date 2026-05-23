"""Collective intelligence engine — coordinates all background intelligence jobs."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.evolution_log import EvolutionLogStore
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
        self._skill_manager = SkillManager(db)
        self._evo_log_store = EvolutionLogStore(db)

    async def start_background_jobs(self) -> None:
        """Start the instinct evolution background loops."""
        from src.instinct_extractor import InstinctExtractor, InstinctStore
        from src.observation import ObservationStore

        obs_store = ObservationStore(self.db)
        instinct_store = InstinctStore(self.db)

        self._extractor = InstinctExtractor(
            db=self.db,
            obs_store=obs_store,
            instinct_store=instinct_store,
            evolution_store=self._evo_log_store,
            skill_manager=self._skill_manager,
            data_root=str(self.data_root),
        )

        # Loop 1: instinct extraction every 10 minutes
        asyncio.create_task(self._extraction_loop())

        # Loop 2: daily eval at 02:00
        asyncio.create_task(self._daily_eval_loop())

        logger.info("Collective intelligence background jobs started")

    async def _extraction_loop(self) -> None:
        import asyncio as _asyncio
        while True:
            try:
                result = await self._extractor.run_once()
                if not result.get("skipped"):
                    logger.info(
                        "Extraction cycle: %d extracted, %d clusters, %d applied, %d proposed",
                        result["extracted"], result["clusters"],
                        result["applied"], result["proposed"],
                    )
            except Exception as exc:
                logger.error("Extraction cycle failed: %s", exc)
            await _asyncio.sleep(10 * 60)  # 10 minutes

    async def _daily_eval_loop(self) -> None:
        import asyncio as _asyncio
        import time
        from src.evolution_signals import EvolutionSignals

        while True:
            now = time.localtime()
            seconds_until_0200 = (
                (24 - now.tm_hour - 2) % 24 * 3600
                - now.tm_min * 60
                - now.tm_sec
            )
            if seconds_until_0200 <= 0:
                seconds_until_0200 = 24 * 3600
            await _asyncio.sleep(seconds_until_0200)

            try:
                signals = EvolutionSignals(
                    self.db, self._evo_log_store, self._skill_manager
                )
                result = await signals.run_daily_eval()
                logger.info(
                    "Daily eval: %d evaluated, %d degraded, %d rolled back",
                    result["evaluated"], result["degraded"], result["rolled_back"],
                )
            except Exception as exc:
                logger.error("Daily eval failed: %s", exc)

"""Collective intelligence engine — coordinates all background intelligence jobs."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.evolution_log import EvolutionLogStore
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
        self._skill_manager = SkillManager(db)
        self._evo_log_store = EvolutionLogStore(db)

    async def start_background_jobs(self) -> "CollectiveIntelligenceEngine":
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

        asyncio.create_task(self._extraction_loop())
        logger.info("Collective intelligence background jobs started")
        return self

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


"""Collective intelligence engine — coordinates all background intelligence jobs."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

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
            except Exception:
                logger.exception("Auto-promotion check failed")
            await asyncio.sleep(2 * 3600)

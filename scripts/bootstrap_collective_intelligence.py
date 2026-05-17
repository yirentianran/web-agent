#!/usr/bin/env python
"""One-time bootstrap: mine historical messages for initial collective intelligence.

Usage:
    uv run python scripts/bootstrap_collective_intelligence.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import Database
from src.pattern_learner import PatternLearner
from src.wiki_generator import WikiGenerator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_ROOT = Path(os.environ.get("DATA_ROOT", "data")).resolve()


async def bootstrap() -> None:
    """Run all bootstrap tasks."""
    db = Database(db_path=DATA_ROOT / "web-agent.db")
    await db.init()
    await db.migrate_collective_intelligence()

    wiki_gen = WikiGenerator(db)
    pattern_learner = PatternLearner(db)

    # 1. Generate initial Wiki pages from all historical feedback
    logger.info("Step 1: Mining historical conversations for Wiki pages...")
    generated = await wiki_gen.mine_and_generate(lookback_hours=8760)  # ~1 year
    logger.info("  Generated %d Wiki pages", len(generated))

    # 2. Run initial pattern analysis
    logger.info("Step 2: Analyzing tool usage patterns...")
    patterns = await pattern_learner.extract_tool_patterns()
    logger.info("  Found %d tool pairs", len(patterns.get("tool_pairs", [])))

    await db.close()
    logger.info("Bootstrap complete!")


if __name__ == "__main__":
    asyncio.run(bootstrap())

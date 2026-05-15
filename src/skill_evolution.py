"""Skill evolution: feedback-driven SKILL.md improvement pipeline.

Analyzes user feedback (from SQLite), determines when a skill should evolve,
and uses the Anthropic API to generate an improved version of the skill prompt.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DATA_ROOT = Path(os.environ.get("DATA_ROOT", "data")).resolve()

SHOULD_EVOLVE_MIN_COUNT = 5
SHOULD_EVOLVE_MAX_RATING = 4.0
HIGH_QUALITY_MIN_RATING = 4


@dataclass(frozen=True)
class FeedbackStats:
    count: int
    average_rating: float
    high_quality_count: int
    rating_distribution: dict[str, int]
    versions: list[str]


@dataclass(frozen=True)
class EvolutionCandidate:
    skill_name: str
    stats: FeedbackStats


class SkillEvolutionManager:
    def __init__(self, db: Any) -> None:
        self.db = db
        self.skills_dir = DATA_ROOT / "shared-skills"

    # ── DB-backed methods ──────────────────────────────────────

    async def db_get_feedback_stats(self, skill_name: str) -> FeedbackStats:
        """Compute stats from SQLite."""
        from src.skill_feedback import DBSkillFeedbackManager

        db_mgr = DBSkillFeedbackManager(db=self.db)
        analytics = await db_mgr.get_analytics(skill_name)
        dist = analytics.get("rating_distribution", {})
        return FeedbackStats(
            count=analytics["total_feedbacks"],
            average_rating=analytics["average_rating"],
            high_quality_count=sum(
                v for k, v in dist.items() if int(k) >= HIGH_QUALITY_MIN_RATING
            ),
            rating_distribution={str(k): v for k, v in sorted(dist.items(), key=lambda x: int(x[0]))},
            versions=[],
        )

    async def db_should_evolve(self, skill_name: str) -> bool:
        """Check evolution criteria using DB data."""
        stats = await self.db_get_feedback_stats(skill_name)
        return (
            stats.count >= SHOULD_EVOLVE_MIN_COUNT
            and stats.average_rating < SHOULD_EVOLVE_MAX_RATING
        )

    async def db_get_evolution_candidates(self) -> list[EvolutionCandidate]:
        """Find evolution candidates from SQLite."""
        from src.skill_feedback import DBSkillFeedbackManager

        db_mgr = DBSkillFeedbackManager(db=self.db)
        candidates_data = await db_mgr.get_evolution_candidates()

        candidates: list[EvolutionCandidate] = []
        for c in candidates_data:
            stats = await self.db_get_feedback_stats(c["skill_name"])
            candidates.append(EvolutionCandidate(skill_name=c["skill_name"], stats=stats))

        candidates.sort(key=lambda c: c.stats.average_rating)
        return candidates

    async def db_get_feedback_for_evolution(
        self, skill_name: str
    ) -> dict[str, list[dict[str, Any]]]:
        """Retrieve feedback for skill evolution using DB-backed manager."""
        from src.skill_feedback import DBSkillFeedbackManager

        db_mgr = DBSkillFeedbackManager(db=self.db)
        return await db_mgr.get_feedback_for_evolution(skill_name)

    async def db_activate_version(
        self,
        skill_name: str,
        version_number: int,
        *,
        skills_dir: Path | None = None,
    ) -> dict[str, Any] | None:
        """Activate a specific pending version."""
        from src.skill_feedback import DBSkillFeedbackManager

        db_mgr = DBSkillFeedbackManager(db=self.db)

        resolved = skills_dir or self.skills_dir or DATA_ROOT / "shared-skills"
        return await db_mgr.activate_version(
            skill_name,
            version_number=version_number,
            skills_dir=resolved,
        )

    async def db_rollback_version(
        self,
        skill_name: str,
        *,
        skills_dir: Path | None = None,
    ) -> dict[str, Any] | None:
        """Rollback to the most recent backup version."""
        from src.skill_feedback import DBSkillFeedbackManager

        db_mgr = DBSkillFeedbackManager(db=self.db)

        resolved = skills_dir or self.skills_dir or DATA_ROOT / "shared-skills"
        return await db_mgr.rollback_version(
            skill_name,
            skills_dir=resolved,
        )

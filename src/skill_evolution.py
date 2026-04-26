"""Skill evolution: feedback-driven SKILL.md improvement pipeline.

Analyzes user feedback, determines when a skill should evolve, and uses
the Anthropic API to generate an improved version of the skill prompt.
"""

from __future__ import annotations

import glob
import json
import os
import time
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
    def __init__(self, data_root: Path = DATA_ROOT, db: Any = None) -> None:
        self.data_root = data_root
        self.db = db
        self.feedback_dir = data_root / "training" / "skill-feedback"
        self.skills_dir = data_root / "shared-skills"

    def _ensure_dirs(self) -> None:
        self.feedback_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def _feedback_files(self, skill_name: str) -> list[Path]:
        pattern = str(self.feedback_dir / f"*_{skill_name}.jsonl")
        return [Path(p) for p in glob.glob(pattern)]

    def _load_feedback(self, skill_name: str) -> list[dict]:
        entries: list[dict] = []
        for fp in self._feedback_files(skill_name):
            try:
                with open(fp, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            entries.append(json.loads(line))
            except (json.JSONDecodeError, OSError):
                continue
        return entries

    def collect_feedback(
        self,
        skill_name: str,
        *,
        rating: int,
        user_id: str = "anonymous",
        comment: str = "",
        session_id: str | None = None,
        version: str = "current",
    ) -> dict:
        """Record a single feedback entry. Rating must be 1-5."""
        if not (1 <= rating <= 5):
            raise ValueError(f"Rating must be between 1 and 5, got {rating}")

        self._ensure_dirs()
        timestamp = time.time()
        entry = {
            "skill_name": skill_name,
            "user_id": user_id,
            "rating": rating,
            "comment": comment[:500],
            "session_id": session_id,
            "version": version,
            "timestamp": timestamp,
        }
        filepath = self.feedback_dir / f"{timestamp:.0f}_{skill_name}.jsonl"
        with open(filepath, "a") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

    def get_feedback_stats(self, skill_name: str) -> FeedbackStats:
        """Compute aggregate stats for a skill's feedback."""
        entries = self._load_feedback(skill_name)
        if not entries:
            return FeedbackStats(
                count=0,
                average_rating=0.0,
                high_quality_count=0,
                rating_distribution={},
                versions=[],
            )

        ratings = [e["rating"] for e in entries if "rating" in e]
        dist: dict[str, int] = {}
        for r in ratings:
            dist[str(r)] = dist.get(str(r), 0) + 1

        versions = sorted(
            {e.get("version", "current") for e in entries if "version" in e}
        )

        return FeedbackStats(
            count=len(ratings),
            average_rating=round(sum(ratings) / len(ratings), 2) if ratings else 0.0,
            high_quality_count=sum(1 for r in ratings if r >= HIGH_QUALITY_MIN_RATING),
            rating_distribution=dist,
            versions=versions,
        )

    def should_evolve(self, skill_name: str) -> bool:
        """Return True if feedback count >= threshold AND avg rating is low enough."""
        stats = self.get_feedback_stats(skill_name)
        return (
            stats.count >= SHOULD_EVOLVE_MIN_COUNT
            and stats.average_rating < SHOULD_EVOLVE_MAX_RATING
        )

    def get_evolution_candidates(self) -> list[EvolutionCandidate]:
        """Return all skills that should evolve, sorted by avg_rating ASC."""
        self._ensure_dirs()
        # Discover all unique skill names from feedback files
        skill_names: set[str] = set()
        for fp in self.feedback_dir.glob("*.jsonl"):
            parts = fp.stem.rsplit("_", 1)
            if len(parts) == 2:
                skill_names.add(parts[1])

        candidates: list[EvolutionCandidate] = []
        for name in skill_names:
            stats = self.get_feedback_stats(name)
            if self.should_evolve(name):
                candidates.append(EvolutionCandidate(skill_name=name, stats=stats))

        candidates.sort(key=lambda c: c.stats.average_rating)
        return candidates

    # ── Async DB-backed methods ──────────────────────────────────────

    async def db_get_feedback_stats(self, skill_name: str) -> FeedbackStats:
        """Compute stats from SQLite. Falls back to file-based if DB not set."""
        if self.db is None:
            return self.get_feedback_stats(skill_name)

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
        if self.db is None:
            return self.get_evolution_candidates()

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

        resolved = skills_dir or self.skills_dir or self.data_root / "shared-skills"
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

        resolved = skills_dir or self.skills_dir or self.data_root / "shared-skills"
        return await db_mgr.rollback_version(
            skill_name,
            skills_dir=resolved,
        )



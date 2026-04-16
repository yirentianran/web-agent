"""Skill feedback collection, rating aggregation, and evolution pipeline.

Collects user feedback per skill, aggregates ratings over time, and suggests
prompt improvements.

Usage:
    from src.skill_feedback import SkillFeedbackManager

    mgr = SkillFeedbackManager()
    mgr.submit_feedback("audit-pdf", user_id="alice", rating=4, comment="Good coverage")
    analytics = mgr.get_analytics("audit-pdf")
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data"))


class SkillFeedbackManager:
    """Per-skill feedback collection and analytics."""

    def __init__(self, data_root: Path = DATA_ROOT) -> None:
        self.feedback_dir = data_root / "training" / "skill-feedback"
        self.feedback_dir.mkdir(parents=True, exist_ok=True)

    def submit_feedback(
        self,
        skill_name: str,
        *,
        user_id: str,
        rating: int,
        comment: str = "",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Submit feedback for a skill. Rating is 1-5."""
        if not 1 <= rating <= 5:
            raise ValueError("Rating must be between 1 and 5")

        entry = {
            "skill_name": skill_name,
            "user_id": user_id,
            "rating": rating,
            "comment": comment[:500],
            "session_id": session_id,
            "timestamp": time.time(),
        }

        feedback_file = self.feedback_dir / f"{time.time()}_{skill_name}.jsonl"
        feedback_file.write_text(json.dumps(entry, ensure_ascii=False))
        return entry

    def get_analytics(self, skill_name: str) -> dict[str, Any]:
        """Get aggregated analytics for a skill."""
        feedbacks = self._load_feedback(skill_name)
        if not feedbacks:
            return {
                "skill_name": skill_name,
                "total_feedbacks": 0,
                "average_rating": 0,
                "rating_distribution": {},
                "recent_comments": [],
            }

        ratings = [f["rating"] for f in feedbacks]
        distribution: dict[int, int] = {}
        for r in ratings:
            distribution[r] = distribution.get(r, 0) + 1

        recent = sorted(
            [f for f in feedbacks if f.get("comment")],
            key=lambda f: f.get("timestamp", 0),
            reverse=True,
        )[:5]

        return {
            "skill_name": skill_name,
            "total_feedbacks": len(feedbacks),
            "average_rating": round(sum(ratings) / len(ratings), 2),
            "rating_distribution": {str(k): v for k, v in sorted(distribution.items())},
            "recent_comments": [
                {"user_id": c["user_id"], "comment": c["comment"], "rating": c["rating"]}
                for c in recent
            ],
        }

    def get_all_analytics(self) -> dict[str, dict[str, Any]]:
        """Get analytics for all skills."""
        skills: set[str] = set()
        for f in self.feedback_dir.glob("*.jsonl"):
            try:
                data = json.loads(f.read_text())
                skills.add(data["skill_name"])
            except (json.JSONDecodeError, KeyError, OSError):
                continue

        return {skill: self.get_analytics(skill) for skill in sorted(skills)}

    def suggest_improvements(self, skill_name: str) -> list[str]:
        """Generate improvement suggestions based on low-rated feedback."""
        feedbacks = self._load_feedback(skill_name)
        suggestions: list[str] = []

        low_rated = [f for f in feedbacks if f["rating"] <= 2]
        if len(low_rated) > len(feedbacks) * 0.5 and len(feedbacks) >= 3:
            suggestions.append(
                f"50%+ of {len(feedbacks)} feedbacks are rated 2 or below. "
                "Consider reviewing the skill's SKILL.md for gaps."
            )

        common_keywords = ["missing", "wrong", "incorrect", "outdated", "confusing"]
        for fb in low_rated:
            comment = fb.get("comment", "").lower()
            for kw in common_keywords:
                if kw in comment:
                    suggestions.append(
                        f"Feedback mentions '{kw}': \"{fb['comment'][:100]}\""
                    )
                    break

        return suggestions

    def _load_feedback(self, skill_name: str) -> list[dict[str, Any]]:
        """Load all feedback entries for a skill."""
        entries: list[dict[str, Any]] = []
        for f in self.feedback_dir.glob(f"*_{skill_name}.jsonl"):
            try:
                entries.append(json.loads(f.read_text()))
            except (json.JSONDecodeError, OSError):
                continue
        return entries

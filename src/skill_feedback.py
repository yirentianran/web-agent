"""Skill feedback collection, rating aggregation, and evolution pipeline.

Collects user feedback per skill, aggregates ratings over time, and suggests
prompt improvements.

Usage:
    # File-based (legacy)
    from src.skill_feedback import SkillFeedbackManager
    mgr = SkillFeedbackManager()
    mgr.submit_feedback("audit-pdf", user_id="alice", rating=4, comment="Good coverage")

    # SQLite-backed (new)
    from src.skill_feedback import DBSkillFeedbackManager
    mgr = DBSkillFeedbackManager(db=db)
    await mgr.submit_feedback("audit-pdf", user_id="alice", rating=4, comment="Good coverage")
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


# ── SQLite-backed feedback manager ─────────────────────────────────


class DBSkillFeedbackManager:
    """Per-skill feedback collection and analytics using SQLite.

    Replaces the file-based SkillFeedbackManager with DB persistence.
    """

    def __init__(self, db: Any) -> None:  # Database from src.database
        self.db = db

    async def submit_feedback(
        self,
        skill_name: str,
        *,
        user_id: str,
        rating: int,
        comment: str = "",
        session_id: str | None = None,
        user_edits: str = "",
        skill_version: str = "",
    ) -> dict[str, Any]:
        """Submit feedback for a skill. Rating is 1-5."""
        if not 1 <= rating <= 5:
            raise ValueError("Rating must be between 1 and 5")

        truncated_comment = comment[:500]
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """INSERT INTO skill_feedback
                   (skill_name, user_id, session_id, rating, comment, skill_version)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (skill_name, user_id, session_id, rating, truncated_comment, skill_version),
            )
            feedback_id = cursor.lastrowid

        return {
            "id": feedback_id,
            "skill_name": skill_name,
            "user_id": user_id,
            "rating": rating,
            "comment": truncated_comment,
            "session_id": session_id,
            "user_edits": user_edits,
            "skill_version": skill_version,
            "timestamp": time.time(),
        }

    async def get_analytics(self, skill_name: str) -> dict[str, Any]:
        """Get aggregated analytics for a skill."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT COUNT(*) as cnt,
                          AVG(rating) as avg_r,
                          SUM(CASE WHEN rating=1 THEN 1 ELSE 0 END) as d1,
                          SUM(CASE WHEN rating=2 THEN 1 ELSE 0 END) as d2,
                          SUM(CASE WHEN rating=3 THEN 1 ELSE 0 END) as d3,
                          SUM(CASE WHEN rating=4 THEN 1 ELSE 0 END) as d4,
                          SUM(CASE WHEN rating=5 THEN 1 ELSE 0 END) as d5
                   FROM skill_feedback WHERE skill_name = ?""",
                (skill_name,),
            )
            row = await cursor.fetchone()

        if not row or row[0] == 0:
            return {
                "skill_name": skill_name,
                "total_feedbacks": 0,
                "average_rating": 0,
                "rating_distribution": {},
                "recent_comments": [],
            }

        total, avg_r, d1, d2, d3, d4, d5 = row
        distribution = {}
        for i, val in enumerate((d1, d2, d3, d4, d5), start=1):
            if val:
                distribution[str(i)] = val

        # Recent comments
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT user_id, comment, rating
                   FROM skill_feedback
                   WHERE skill_name = ? AND comment != ''
                   ORDER BY created_at DESC LIMIT 5""",
                (skill_name,),
            )
            comment_rows = await cursor.fetchall()

        return {
            "skill_name": skill_name,
            "total_feedbacks": total,
            "average_rating": round(avg_r, 2),
            "rating_distribution": distribution,
            "recent_comments": [
                {"user_id": r[0], "comment": r[1], "rating": r[2]}
                for r in comment_rows
            ],
        }

    async def get_all_analytics(self) -> dict[str, dict[str, Any]]:
        """Get analytics for all skills."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT DISTINCT skill_name FROM skill_feedback"
            )
            rows = await cursor.fetchall()

        skills = {r[0] for r in rows}
        result: dict[str, dict[str, Any]] = {}
        for skill in sorted(skills):
            result[skill] = await self.get_analytics(skill)
        return result

    async def get_user_feedback(self, user_id: str) -> list[dict[str, Any]]:
        """Get all feedback entries for a user."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT id, skill_name, user_id, session_id, rating,
                          comment, user_edits, skill_version, created_at
                   FROM skill_feedback WHERE user_id = ?
                   ORDER BY created_at DESC""",
                (user_id,),
            )
            rows = await cursor.fetchall()

        return [
            {
                "id": r[0],
                "skill_name": r[1],
                "user_id": r[2],
                "session_id": r[3],
                "rating": r[4],
                "comment": r[5],
                "user_edits": r[6],
                "skill_version": r[7],
                "timestamp": r[8],
            }
            for r in rows
        ]

    async def get_user_feedback_stats(self, user_id: str) -> dict[str, Any]:
        """Get feedback stats grouped by skill for a user."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT skill_name, COUNT(*) as cnt, AVG(rating) as avg_r
                   FROM skill_feedback WHERE user_id = ?
                   GROUP BY skill_name
                   ORDER BY cnt DESC""",
                (user_id,),
            )
            rows = await cursor.fetchall()

        stats = [
            {
                "skill_name": r[0],
                "count": r[1],
                "avg_rating": round(r[2], 2),
            }
            for r in rows
        ]

        total = sum(s["count"] for s in stats)
        return {"stats": stats, "total_count": total}

    async def get_evolution_candidates(self) -> list[dict[str, Any]]:
        """Find skills with low average rating and sufficient feedback."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT skill_name, COUNT(*) as cnt, AVG(rating) as avg_r
                   FROM skill_feedback
                   GROUP BY skill_name
                   HAVING cnt >= 10 AND avg_r < 4.5"""
            )
            rows = await cursor.fetchall()

        return [
            {"skill_name": r[0], "count": r[1], "avg_rating": round(r[2], 2)}
            for r in rows
        ]

    async def migrate_from_jsonl(self, feedback_dir: Path) -> int:
        """Migrate existing JSONL files to SQLite. Returns count of migrated entries."""
        migrated = 0
        for f in feedback_dir.glob("*.jsonl"):
            try:
                content = f.read_text(encoding="utf-8")
                for line in content.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    await self.submit_feedback(
                        entry["skill_name"],
                        user_id=entry.get("user_id", "anonymous"),
                        rating=entry["rating"],
                        comment=entry.get("comment", ""),
                        session_id=entry.get("session_id"),
                    )
                    migrated += 1
                f.unlink()
            except (json.JSONDecodeError, OSError, KeyError):
                continue
        return migrated

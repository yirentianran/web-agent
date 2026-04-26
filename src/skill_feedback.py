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
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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
        conversation_snippet: str = "",
    ) -> dict[str, Any]:
        """Submit feedback for a skill. Rating is 1-5."""
        if not 1 <= rating <= 5:
            raise ValueError("Rating must be between 1 and 5")

        truncated_comment = comment[:500]
        truncated_snippet = conversation_snippet[:2000]
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """INSERT INTO skill_feedback
                   (skill_name, user_id, session_id, rating, comment, user_edits, skill_version, conversation_snippet)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (skill_name, user_id, session_id, rating, truncated_comment, user_edits, skill_version, truncated_snippet),
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

    async def get_all_feedback(self) -> list[dict[str, Any]]:
        """Get all feedback entries across all users."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT id, skill_name, user_id, session_id, rating,
                          comment, user_edits, skill_version, created_at
                   FROM skill_feedback
                   ORDER BY created_at DESC"""
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

    async def suggest_improvements(self, skill_name: str) -> list[str]:
        """Generate improvement suggestions based on low-rated feedback from DB."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM skill_feedback WHERE skill_name = ?",
                (skill_name,),
            )
            row = await cursor.fetchone()
            total = row[0] if row else 0

        if total < 3:
            return []

        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT rating, comment FROM skill_feedback
                   WHERE skill_name = ? AND rating <= 2 AND comment != ''
                   ORDER BY created_at DESC""",
                (skill_name,),
            )
            rows = await cursor.fetchall()

        suggestions: list[str] = []
        low_count = len(rows)
        if low_count > total * 0.5:
            suggestions.append(
                f"50%+ of {total} feedbacks are rated 2 or below. "
                "Consider reviewing the skill's SKILL.md for gaps."
            )

        common_keywords = ["missing", "wrong", "incorrect", "outdated", "confusing"]
        for r in rows:
            comment = r[1].lower()
            for kw in common_keywords:
                if kw in comment:
                    suggestions.append(
                        f"Feedback mentions '{kw}': \"{r[1][:100]}\""
                    )
                    break

        return suggestions

    async def get_evolution_candidates(self) -> list[dict[str, Any]]:
        """Find skills with low average rating and sufficient feedback."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT skill_name, COUNT(*) as cnt, AVG(rating) as avg_r
                   FROM skill_feedback
                   GROUP BY skill_name
                   HAVING cnt >= 5 AND avg_r < 4.0"""
            )
            rows = await cursor.fetchall()

        return [
            {"skill_name": r[0], "count": r[1], "avg_rating": round(r[2], 2)}
            for r in rows
        ]

    async def get_feedback_for_evolution(
        self, skill_name: str
    ) -> dict[str, list[dict[str, Any]]]:
        """Retrieve high-quality and low-quality feedback entries for skill evolution."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT rating, comment, user_edits
                   FROM skill_feedback
                   WHERE skill_name = ? AND comment != ''
                   ORDER BY created_at DESC""",
                (skill_name,),
            )
            rows = await cursor.fetchall()

        entries = [
            {"rating": r[0], "comment": r[1], "user_edits": r[2]}
            for r in rows
        ]
        return {
            "high_quality": [e for e in entries if e["rating"] >= 4],
            "low_rated": [e for e in entries if e["rating"] <= 2 and e["comment"]],
            "user_edits": [e for e in entries if e.get("user_edits")],
        }

    async def activate_version(
        self,
        skill_name: str,
        version_number: int,
        skills_dir: Path,
    ) -> dict[str, Any] | None:
        """Activate a specific pending version, replacing SKILL.md.

        Backs up the current SKILL.md before replacing.
        Returns dict with activated=True, version_number, backup path.
        """
        skill_dir = skills_dir / skill_name
        skill_file = skill_dir / "SKILL.md"
        version_path = skill_dir / f"SKILL_v{version_number}.md"

        if not version_path.exists():
            return None
        if not skill_file.exists():
            return None

        new_content = version_path.read_text()

        # Backup current version
        existing_backups = list(skill_dir.glob("SKILL_backup_v*.md"))
        next_backup = len(existing_backups) + 1
        backup_path = skill_dir / f"SKILL_backup_v{next_backup}.md"
        skill_file.rename(backup_path)

        # Activate new version
        version_path.rename(skill_file)

        return {
            "activated": True,
            "version_number": version_number,
            "backup": str(backup_path),
        }

    async def rollback_version(
        self,
        skill_name: str,
        skills_dir: Path,
    ) -> dict[str, Any] | None:
        """Rollback to the most recent backup version.

        Returns dict with rolled_back=True and restored_version.
        """
        skill_dir = skills_dir / skill_name
        skill_file = skill_dir / "SKILL.md"

        if not skill_file.exists():
            return None

        backups = sorted(skill_dir.glob("SKILL_backup_v*.md"))
        if not backups:
            return None

        # Restore the latest backup
        latest_backup = backups[-1]
        current_backup_path = skill_dir / f"SKILL_backup_current_{time.time()}.md"
        skill_file.rename(current_backup_path)
        latest_backup.rename(skill_file)

        version_name = latest_backup.stem.replace("SKILL_backup_", "")
        return {
            "rolled_back": True,
            "restored_version": version_name,
        }

    async def list_versions(
        self, skill_name: str, skills_dir: Path
    ) -> list[dict[str, Any]]:
        """List all version files for a skill.

        Supports both legacy file-based versions (SKILL_v*.md) and
        new directory-based versions (versions/vN/).
        """
        skill_dir = skills_dir / skill_name
        if not skill_dir.exists():
            return []

        versions: list[dict[str, Any]] = []

        # New directory-based versions
        versions_dir = skill_dir / "versions"
        if versions_dir.exists():
            for v_dir in sorted(versions_dir.iterdir()):
                if v_dir.is_dir() and v_dir.name.startswith("v"):
                    skill_md = v_dir / "SKILL.md"
                    if skill_md.exists():
                        content = skill_md.read_text()
                        stat = v_dir.stat()
                        file_count = len(list(v_dir.rglob("*")))
                        versions.append({
                            "name": v_dir.name,
                            "size": len(content),
                            "created_at": stat.st_mtime,
                            "is_directory": True,
                            "file_count": file_count,
                        })

        # Legacy file-based versions (fallback)
        for f in sorted(skill_dir.iterdir()):
            if f.name.startswith("SKILL_v") and f.name.endswith(".md") and f.is_file():
                content = f.read_text()
                stat = f.stat()
                versions.append({
                    "name": f.stem,
                    "size": len(content),
                    "created_at": stat.st_mtime,
                    "is_directory": False,
                })

        return versions

    async def get_version_content(
        self, skill_name: str, version_name: str, skills_dir: Path
    ) -> str | None:
        """Get the content of a specific version file.

        Supports both legacy file-based versions and new directory-based versions.
        """
        skill_dir = skills_dir / skill_name
        if version_name == "current":
            target = skill_dir / "SKILL.md"
        else:
            # Try directory-based version first
            target = skill_dir / "versions" / version_name / "SKILL.md"
            if not target.exists():
                # Fall back to file-based version
                target = skill_dir / f"{version_name}.md"

        if not target.exists():
            return None
        return target.read_text()

    async def list_version_files(
        self, skill_name: str, version_number: int, skills_dir: Path
    ) -> list[dict[str, Any]] | None:
        """List all files in a specific version directory.

        Returns None if the version doesn't exist.
        """
        skill_dir = skills_dir / skill_name
        version_dir = skill_dir / "versions" / f"v{version_number}"

        if not version_dir.exists():
            return None

        files = []
        for f in version_dir.rglob("*"):
            if f.is_file():
                rel = str(f.relative_to(version_dir))
                files.append({
                    "path": rel,
                    "size": f.stat().st_size,
                    "is_skill_md": rel == "SKILL.md",
                })
        return files

    async def activate_directory_version(
        self,
        skill_name: str,
        version_number: int,
        skills_dir: Path,
    ) -> dict[str, Any] | None:
        """Activate a directory-based version.

        Copies all files from versions/v{N}/ to the skill root,
        backing up current files first.
        """
        import shutil

        skill_dir = skills_dir / skill_name
        version_dir = skill_dir / "versions" / f"v{version_number}"

        if not version_dir.exists():
            return None

        # Backup current files
        backup_dir = skill_dir / "backups" / f"before_v{version_number}"
        backup_dir.mkdir(parents=True, exist_ok=True)

        for f in skill_dir.iterdir():
            if f.is_file() and f.name.startswith("SKILL"):
                shutil.copy2(f, backup_dir / f.name)

        # Copy version files to skill root
        for f in version_dir.rglob("*"):
            if f.is_file():
                dest = skill_dir / f.relative_to(version_dir)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)

        # Update skill-meta.json with evolution info
        meta_path = skill_dir / "skill-meta.json"
        meta: dict[str, Any] = {}
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
        meta["last_evolved_at"] = datetime.now(timezone.utc).isoformat()
        meta["current_version"] = version_number
        meta["evolution_history"] = meta.get("evolution_history", [])
        meta["evolution_history"].append({
            "version": version_number,
            "activated_at": datetime.now(timezone.utc).isoformat(),
            "source": "admin_review",
        })
        meta_path.write_text(json.dumps(meta, indent=2))

        return {
            "activated": True,
            "version_number": version_number,
            "backup": str(backup_dir),
        }

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

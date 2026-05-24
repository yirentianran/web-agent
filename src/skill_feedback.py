"""Skill feedback collection, rating aggregation, and evolution pipeline.

All feedback is stored in SQLite (skill_feedback table). No file-based storage.

Usage:
    from src.skill_feedback import DBSkillFeedbackManager

    mgr = DBSkillFeedbackManager(db=db)
    await mgr.submit_feedback("audit-pdf", user_id="alice", rating=4, comment="Good coverage")
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC
from typing import Any

from src.cost import get_flash_model

logger = logging.getLogger(__name__)


class DBSkillFeedbackManager:
    """Per-skill feedback collection and analytics using SQLite."""

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
                (
                    skill_name,
                    user_id,
                    session_id,
                    rating,
                    truncated_comment,
                    user_edits,
                    skill_version,
                    truncated_snippet,
                ),
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
        skills_dir,
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
        skills_dir,
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
        self, skill_name: str, skills_dir
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
        self, skill_name: str, version_name: str, skills_dir
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
        self, skill_name: str, version_number: int, skills_dir
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
        skills_dir,
    ) -> dict[str, Any] | None:
        """Activate a directory-based version.

        Copies all files from versions/v{N}/ to the skill root,
        backing up current files first.
        """
        import shutil
        from datetime import datetime

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
        meta["last_evolved_at"] = datetime.now(UTC).isoformat()
        meta["current_version"] = version_number
        meta["evolution_history"] = meta.get("evolution_history", [])
        meta["evolution_history"].append({
            "version": version_number,
            "activated_at": datetime.now(UTC).isoformat(),
            "source": "admin_review",
        })
        meta_path.write_text(json.dumps(meta, indent=2))

        return {
            "activated": True,
            "version_number": version_number,
            "backup": str(backup_dir),
        }

    @staticmethod
    def next_version_number(versions_dir: Any) -> int:
        """Return the next version number based on existing version directories.

        Uses max(existing_versions) + 1 rather than len(existing_versions) + 1
        to avoid collisions when versions are deleted.
        """
        from pathlib import Path

        if not isinstance(versions_dir, Path) or not versions_dir.exists():
            return 1
        max_ver = 0
        for entry in versions_dir.iterdir():
            if entry.is_dir() and entry.name.startswith("v"):
                try:
                    ver = int(entry.name[1:])
                    max_ver = max(max_ver, ver)
                except ValueError:
                    continue
        return max_ver + 1

    async def create_version(
        self,
        skill_name: str,
        *,
        new_content: str,
        change_summary: str,
        created_by: str = "auto-evolve",
        skills_dir=None,
    ) -> dict[str, Any] | None:
        """Create a new skill version with backup and versioning.

        1. Reads current SKILL.md from the skills directory
        2. Computes next version number
        3. Backs up current version as SKILL_backup_v{N}.md
        4. Creates versions/v{N}/ directory with new SKILL.md
        5. Records version in skill_versions table
        6. Updates skills.version field
        """
        import shutil
        from pathlib import Path

        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT path FROM skills WHERE skill_name = ?", (skill_name,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None

            skill_dir = Path(row["path"])
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                return None

            versions_dir = skill_dir / "versions"
            next_version = self.next_version_number(versions_dir)

            # Backup current version
            backup_path = skill_dir / f"SKILL_backup_v{next_version}.md"
            shutil.copy2(skill_file, backup_path)

            # Create version directory with new content
            version_dir = skill_dir.with_name(f"{skill_dir.name}@v{next_version}")
            version_dir.mkdir(parents=True, exist_ok=True)
            (version_dir / "SKILL.md").write_text(new_content)

            await conn.execute(
                """INSERT INTO skill_versions
                   (skill_name, version_number, path, change_summary, created_by, file_count, status)
                   VALUES (?, ?, ?, ?, ?, 1, 'active')""",
                (skill_name, next_version, str(version_dir), change_summary, created_by),
            )
            await conn.execute(
                "UPDATE skills SET version = ?, updated_at = strftime('%s', 'now') WHERE skill_name = ?",
                (f"v{next_version}", skill_name),
            )
            await conn.commit()

        return {
            "skill_name": skill_name,
            "version": next_version,
            "backup": str(backup_path),
        }

    async def apply_user_edits(
        self,
        skill_name: str,
        user_edits: str,
        *,
        skills_dir=None,
    ) -> dict[str, Any] | None:
        """Safely apply user-provided edits to a skill.

        Creates a new version with the user's edits applied to SKILL.md.
        This is the safest form of auto-evolution.
        """
        import os
        from pathlib import Path

        data_root = Path(os.environ.get("DATA_ROOT", "data")).resolve()
        resolved = skills_dir or data_root / "shared-skills"

        return await self.create_version(
            skill_name,
            new_content=user_edits,
            change_summary="Applied user-provided edits",
            created_by="auto-evolve-user-edits",
            skills_dir=resolved,
        )

    async def auto_fix_skill(
        self,
        skill_name: str,
        bugs: list[str],
        *,
        skills_dir=None,
    ) -> dict[str, Any] | None:
        """Auto-generate a fix for a skill based on identified bugs.

        Calls an LLM to generate corrected SKILL.md content, then applies
        it using the same versioning pipeline.
        """
        import os
        from pathlib import Path

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning("AUTO_FIX skipped: ANTHROPIC_API_KEY not set")
            return None

        # Read current skill content
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT path FROM skills WHERE skill_name = ?", (skill_name,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            skill_dir = Path(row["path"])
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                return None
            current_content = skill_file.read_text()

        bug_list = "\n".join(f"- {b}" for b in bugs)
        prompt = (
            f"You are fixing a skill definition for an AI agent system.\n\n"
            f"Skill name: {skill_name}\n\n"
            f"Current SKILL.md content:\n```markdown\n{current_content}\n```\n\n"
            f"Identified bugs:\n{bug_list}\n\n"
            f"Return ONLY the fixed SKILL.md content. Keep the same structure and format. "
            f"Fix all the identified bugs. Do not add explanations."
        )

        try:
            import httpx

            resp = await httpx.AsyncClient().post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": get_flash_model(),
                    "max_tokens": 4000,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            data = resp.json()
            from src.text_utils import strip_markdown_fences
            fixed_content = data["content"][0]["text"]
            fixed_content = strip_markdown_fences(fixed_content)

            # Apply the fix
            return await self.apply_user_edits(skill_name, fixed_content)
        except Exception as e:
            logger.error("AUTO_FIX failed for %s: %s", skill_name, e)
            return None

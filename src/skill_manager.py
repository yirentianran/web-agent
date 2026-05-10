"""Database-backed skill registry, usage tracking, and version metadata.

Follows the same pattern as src/skill_feedback.py (DBSkillFeedbackManager).

Usage:
    from src.skill_manager import SkillManager

    mgr = SkillManager(db=_db)
    await mgr.register_skill("my-skill", source="personal", ...)
    await mgr.get_usage_stats("my-skill")
"""

from __future__ import annotations

import json
from typing import Any


class SkillManager:
    """Database-backed skill registry, usage tracking, and version metadata."""

    def __init__(self, db: Any) -> None:
        self.db = db

    # ── Registry ──────────────────────────────────────────────────────

    async def register_skill(
        self,
        skill_name: str,
        source: str,
        owner_id: str,
        description: str = "",
        category: str = "",
        tags: list[str] | None = None,
        path: str = "",
    ) -> None:
        """Register a skill. Idempotent — ON CONFLICT updates metadata."""
        tags_json = json.dumps(tags or [])
        async with self.db.connection() as conn:
            await conn.execute(
                """INSERT INTO skills (skill_name, source, owner_id, description, category, tags, path)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(skill_name) DO UPDATE SET
                       source=excluded.source, owner_id=excluded.owner_id,
                       description=excluded.description, category=excluded.category,
                       tags=excluded.tags, path=excluded.path, updated_at=strftime('%s', 'now')""",
                (skill_name, source, owner_id, description, category, tags_json, path),
            )
            await conn.commit()

    async def update_skill_meta(self, skill_name: str, **kwargs: Any) -> None:
        """Update arbitrary metadata fields for a skill."""
        allowed = {"description", "category", "tags", "status", "version", "owner_id"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        if "tags" in fields and isinstance(fields["tags"], list):
            fields["tags"] = json.dumps(fields["tags"])
        cols = ", ".join(f"{k} = ?" for k in fields)
        cols += ", updated_at = strftime('%s', 'now')"
        async with self.db.connection() as conn:
            await conn.execute(
                f"UPDATE skills SET {cols} WHERE skill_name = ?",
                (*fields.values(), skill_name),
            )
            await conn.commit()

    async def get_skill(self, skill_name: str) -> dict[str, Any] | None:
        """Get a single skill's metadata."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM skills WHERE skill_name = ?", (skill_name,)
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        result = dict(row)
        try:
            result["tags"] = json.loads(result.get("tags", "[]"))
        except (json.JSONDecodeError, TypeError):
            result["tags"] = []
        return result

    async def list_skills(
        self,
        source: str | None = None,
        category: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        owner: str | None = None,
    ) -> list[dict[str, Any]]:
        """List skills with optional filters."""
        conditions: list[str] = []
        params: list[Any] = []
        if source:
            conditions.append("source = ?")
            params.append(source)
        if category:
            conditions.append("category = ?")
            params.append(category)
        if tag:
            conditions.append("tags LIKE ?")
            params.append(f'%"{tag}"%')
        if status:
            conditions.append("status = ?")
            params.append(status)
        if owner:
            conditions.append("owner_id = ?")
            params.append(owner)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT * FROM skills {where} ORDER BY updated_at DESC", params
            )
            rows = await cursor.fetchall()
        results = []
        for row in rows:
            d = dict(row)
            try:
                d["tags"] = json.loads(d.get("tags", "[]"))
            except (json.JSONDecodeError, TypeError):
                d["tags"] = []
            results.append(d)
        return results

    async def delete_skill(self, skill_name: str, *, delete_files: bool = False) -> None:
        """Delete skill metadata. Optionally also remove files."""
        async with self.db.connection() as conn:
            await conn.execute(
                "UPDATE skills SET status = 'deprecated' WHERE skill_name = ?",
                (skill_name,),
            )
            await conn.commit()
        if delete_files:
            await self._delete_skill_files(skill_name)

    async def _delete_skill_files(self, skill_name: str) -> None:
        """Remove skill files from filesystem. Only for admin hard-delete."""
        import os
        from pathlib import Path

        DATA_ROOT = Path(os.environ.get("DATA_ROOT", "data")).resolve()
        shared = DATA_ROOT / "shared-skills" / skill_name
        if shared.exists():
            import shutil
            shutil.rmtree(shared)
            return
        # Personal skills require owner_id to find — skip in DB-only delete

    # ── Usage ─────────────────────────────────────────────────────────

    async def record_usage(
        self,
        skill_name: str,
        user_id: str = "",
        session_id: str = "",
        version_number: int = 0,
        action: str = "use",
    ) -> None:
        """Record a skill usage event. Fire-and-forget — never raises."""
        try:
            async with self.db.connection() as conn:
                await conn.execute(
                    "INSERT INTO skill_usage (skill_name, user_id, session_id, version_number, action) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (skill_name, user_id, session_id, version_number, action),
                )
                await conn.commit()
        except Exception:
            pass  # DB unavailable — don't block agent

    async def get_usage_stats(self, skill_name: str) -> dict[str, Any]:
        """Get usage statistics for a skill."""
        async with self.db.connection() as conn:
            # Total uses
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM skill_usage WHERE skill_name = ?", (skill_name,)
            )
            row = await cursor.fetchone()
            total_uses = row[0] if row else 0

            # Unique users
            cursor = await conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM skill_usage WHERE skill_name = ? AND user_id != ''",
                (skill_name,),
            )
            row = await cursor.fetchone()
            unique_users = row[0] if row else 0

            # Recent sessions (last 5)
            cursor = await conn.execute(
                "SELECT DISTINCT session_id FROM skill_usage "
                "WHERE skill_name = ? AND session_id != '' "
                "ORDER BY created_at DESC LIMIT 5",
                (skill_name,),
            )
            rows = await cursor.fetchall()
            recent_sessions = [r[0] for r in rows]

            # Per-version breakdown
            cursor = await conn.execute(
                "SELECT version_number, COUNT(*) as cnt FROM skill_usage "
                "WHERE skill_name = ? GROUP BY version_number ORDER BY cnt DESC",
                (skill_name,),
            )
            rows = await cursor.fetchall()
            version_breakdown = [{"version": r[0], "uses": r[1]} for r in rows]

        return {
            "skill_name": skill_name,
            "total_uses": total_uses,
            "unique_users": unique_users,
            "recent_sessions": recent_sessions,
            "version_breakdown": version_breakdown,
        }

    async def get_top_skills(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get most-used skills by usage count."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT skill_name, COUNT(*) as cnt, COUNT(DISTINCT user_id) as users "
                "FROM skill_usage GROUP BY skill_name ORDER BY cnt DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
        return [{"skill_name": r[0], "uses": r[1], "unique_users": r[2]} for r in rows]

    # ── Versions ──────────────────────────────────────────────────────

    async def record_version(
        self,
        skill_name: str,
        version_number: int,
        path: str,
        change_summary: str = "",
        created_by: str = "user",
        file_count: int = 1,
    ) -> None:
        """Record a new skill version with its directory path."""
        async with self.db.connection() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO skill_versions "
                "(skill_name, version_number, path, change_summary, created_by, file_count) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (skill_name, version_number, path, change_summary, created_by, file_count),
            )
            await conn.commit()

    async def activate_version(self, skill_name: str, version_number: int) -> dict[str, Any] | None:
        """Activate a version: replace current SKILL.md with the version's content.

        Version directories are flat siblings: {skill_name}@vN/
        Activation: copy SKILL.md from version dir to main skill dir,
        record in DB.
        """
        import shutil
        from pathlib import Path

        async with self.db.connection() as conn:
            # Get version path
            cursor = await conn.execute(
                "SELECT path FROM skill_versions WHERE skill_name = ? AND version_number = ?",
                (skill_name, version_number),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            version_dir = Path(row[0])
            if not version_dir.exists():
                return None

            # Find the skill dir (parent of this version dir)
            parent_dir = version_dir.parent
            # Derive skill dir from version dir name: code-review@v2 → code-review
            skill_dir_name = version_dir.name.rsplit("@v", 1)[0]
            skill_dir = parent_dir / skill_dir_name
            if not skill_dir.exists():
                return None

            # Backup current SKILL.md
            current_file = skill_dir / "SKILL.md"
            version_file = version_dir / "SKILL.md"
            if current_file.exists():
                existing_backups = list(skill_dir.glob("SKILL_backup_v*.md"))
                next_backup = len(existing_backups) + 1
                backup_path = skill_dir / f"SKILL_backup_v{next_backup}.md"
                current_file.rename(backup_path)

            # Copy new version into place
            shutil.copy2(version_file, current_file)

            # Deactivate all, activate target
            await conn.execute(
                "UPDATE skill_versions SET status = 'pending' WHERE skill_name = ?",
                (skill_name,),
            )
            await conn.execute(
                "UPDATE skill_versions SET status = 'active' "
                "WHERE skill_name = ? AND version_number = ?",
                (skill_name, version_number),
            )
            await conn.execute(
                "UPDATE skills SET version = ?, updated_at = strftime('%s', 'now') "
                "WHERE skill_name = ?",
                (f"v{version_number}", skill_name),
            )
            await conn.commit()
        return {
            "activated": True,
            "version_number": version_number,
            "skill_dir": str(skill_dir),
        }

    async def rollback_version(self, skill_name: str) -> dict[str, Any] | None:
        """Rollback to most recent backup SKILL file."""
        from pathlib import Path

        # Find the skill dir
        async with self.db.connection() as conn:
            cursor = await conn.execute("SELECT path FROM skills WHERE skill_name = ?", (skill_name,))
            row = await cursor.fetchone()
            if row is None:
                return None
            skill_dir = Path(row["path"])

        backups = sorted(skill_dir.glob("SKILL_backup_v*.md"))
        if not backups:
            return None
        latest_backup = backups[-1]
        current_file = skill_dir / "SKILL.md"
        if current_file.exists():
            existing_count = len(list(skill_dir.glob("SKILL_backup_v*.md")))
            current_file.rename(skill_dir / f"SKILL_backup_v{existing_count + 1}.md")
        latest_backup.rename(current_file)

        # Update DB status
        async with self.db.connection() as conn:
            await conn.execute(
                "UPDATE skill_versions SET status = 'rolled_back' WHERE skill_name = ?",
                (skill_name,),
            )
            await conn.commit()
        return {"rolled_back": True}

    async def list_versions(self, skill_name: str) -> list[dict[str, Any]]:
        """List all versions for a skill."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM skill_versions WHERE skill_name = ? "
                "ORDER BY version_number DESC",
                (skill_name,),
            )
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

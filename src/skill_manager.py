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
        """Register a skill. Idempotent per (skill_name, source) — ON CONFLICT updates metadata."""
        tags_json = json.dumps(tags or [])
        async with self.db.connection() as conn:
            await conn.execute(
                """INSERT INTO skills (skill_name, source, owner_id, description, category, tags, path)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(skill_name, source) DO UPDATE SET
                       owner_id=excluded.owner_id,
                       description=excluded.description, category=excluded.category,
                       tags=excluded.tags, path=excluded.path, status='active',
                       updated_at=strftime('%s', 'now')""",
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

    # ── Auto-Promotion ──────────────────────────────────────────────

    AUTO_PROMOTE_MIN_FEEDBACK = 5       # Minimum feedback entries
    AUTO_PROMOTE_MIN_USERS = 3          # Minimum distinct users giving feedback
    AUTO_PROMOTE_MIN_AVG_RATING = 4.0   # Minimum average rating
    AUTO_PROMOTE_WINDOW_DAYS = 30       # Time window for counting

    async def check_auto_promotion(self) -> list[dict[str, Any]]:
        """Scan personal skills for auto-promotion candidates.

        Returns skills that meet all thresholds:
        - feedback_count >= AUTO_PROMOTE_MIN_FEEDBACK
        - unique_users >= AUTO_PROMOTE_MIN_USERS
        - avg_rating >= AUTO_PROMOTE_MIN_AVG_RATING
        - within AUTO_PROMOTE_WINDOW_DAYS

        Uses feedback count (not load-time usage) as the engagement metric,
        since load-time recording was removed for accuracy.
        """
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT s.skill_name, s.owner_id,
                          COUNT(DISTINCT sf.id) as feedback_count,
                          COUNT(DISTINCT sf.user_id) as unique_users,
                          AVG(sf.rating) as avg_rating
                   FROM skills s
                   JOIN skill_feedback sf ON sf.skill_name = s.skill_name
                   WHERE s.source = 'personal'
                     AND s.status = 'active'
                     AND sf.created_at > strftime('%s', 'now') - (? * 86400)
                   GROUP BY s.skill_name, s.owner_id
                   HAVING feedback_count >= ?
                      AND unique_users >= ?
                      AND avg_rating >= ?""",
                (
                    self.AUTO_PROMOTE_WINDOW_DAYS,
                    self.AUTO_PROMOTE_MIN_FEEDBACK,
                    self.AUTO_PROMOTE_MIN_USERS,
                    self.AUTO_PROMOTE_MIN_AVG_RATING,
                ),
            )
            rows = await cursor.fetchall()

        candidates = []
        for row in rows:
            candidates.append({
                "skill_name": row[0],
                "owner_id": row[1],
                "uses_count": row[2],
                "unique_users": row[3],
                "avg_rating": row[4],
            })

        # Mark candidates in promotion queue
        for c in candidates:
            async with self.db.connection() as conn:
                await conn.execute(
                    """INSERT OR IGNORE INTO skill_promotion_queue
                       (skill_name, original_owner_id, uses_count, unique_users_count, avg_rating)
                       VALUES (?, ?, ?, ?, ?)""",
                    (c["skill_name"], c["owner_id"], c["uses_count"],
                     c["unique_users"], c["avg_rating"]),
                )
                await conn.commit()

        return candidates

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

    # ── Filesystem Migration ──────────────────────────────────────────

    async def migrate_from_filesystem(self) -> dict[str, int]:
        """Scan filesystem and register all skills not yet in DB.

        Also migrates legacy nested versions (SKILL_v*.md, versions/vN/)
        to flat {skill_name}@vN/ directories.

        Returns dict with counts: {registered: N, versions_migrated: N}
        """
        import os
        import shutil
        from pathlib import Path

        DATA_ROOT = Path(os.environ.get("DATA_ROOT", "data")).resolve()
        registered = 0
        versions_migrated = 0

        # Scan shared skills
        shared_dir = DATA_ROOT / "shared-skills"
        if shared_dir.exists():
            for entry in sorted(shared_dir.iterdir()):
                if not entry.is_dir() or entry.is_symlink():
                    continue
                if "@v" in entry.name:
                    continue
                if not (entry / "SKILL.md").exists():
                    continue
                existing = await self.get_skill(entry.name)
                if existing is not None:
                    continue
                meta = self._read_skill_meta(entry)
                await self.register_skill(
                    skill_name=entry.name,
                    source="shared",
                    owner_id=meta.get("owner", ""),
                    description=meta.get("description", ""),
                    category="",
                    tags=[],
                    path=str(entry),
                )
                registered += 1
                versions_migrated += await self._migrate_legacy_versions(entry)

        # Scan personal skills
        users_dir = DATA_ROOT / "users"
        if users_dir.exists():
            for user_dir in sorted(users_dir.iterdir()):
                if not user_dir.is_dir():
                    continue
                skill_base = user_dir / "workspace" / ".claude" / "skills"
                if not skill_base.exists():
                    continue
                for entry in sorted(skill_base.iterdir()):
                    if not entry.is_dir() or entry.is_symlink():
                        continue
                    if "@v" in entry.name:
                        continue
                    if not (entry / "SKILL.md").exists():
                        continue
                    existing = await self.get_skill(entry.name)
                    if existing is not None:
                        continue
                    meta = self._read_skill_meta(entry)
                    await self.register_skill(
                        skill_name=entry.name,
                        source="personal",
                        owner_id=user_dir.name,
                        description=meta.get("description", ""),
                        category="",
                        tags=[],
                        path=str(entry),
                    )
                    registered += 1
                    versions_migrated += await self._migrate_legacy_versions(entry)

        return {"registered": registered, "versions_migrated": versions_migrated}

    @staticmethod
    def _read_skill_meta(skill_dir: Path) -> dict[str, str]:
        """Read skill-meta.json, return dict with defaults."""
        meta_path = skill_dir / "skill-meta.json"
        if meta_path.exists():
            try:
                return json.loads(meta_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    @staticmethod
    async def _migrate_legacy_versions(skill_dir: Path) -> int:
        """Migrate legacy SKILL_v*.md and versions/vN/ to flat @vN dirs.

        Returns count of migrated versions.
        """
        import re

        migrated = 0

        # Legacy file-based: SKILL_v1.md, SKILL_v2.md, etc.
        for f in sorted(skill_dir.glob("SKILL_v*.md")):
            if not f.is_file():
                continue
            m = re.match(r"SKILL_v(\d+)\.md", f.name)
            if not m:
                continue
            version_number = int(m.group(1))
            version_dir = skill_dir.with_name(f"{skill_dir.name}@v{version_number}")
            version_dir.mkdir(parents=True, exist_ok=True)
            f.rename(version_dir / "SKILL.md")
            migrated += 1

        # Legacy directory-based: versions/v1/, versions/v2/, etc.
        legacy_versions_dir = skill_dir / "versions"
        if legacy_versions_dir.exists():
            for v_dir in sorted(legacy_versions_dir.iterdir()):
                if not v_dir.is_dir() or not v_dir.name.startswith("v"):
                    continue
                try:
                    version_number = int(v_dir.name[1:])
                except ValueError:
                    continue
                new_dir = skill_dir.with_name(f"{skill_dir.name}@v{version_number}")
                if new_dir.exists():
                    for src_file in v_dir.rglob("*"):
                        if src_file.is_file():
                            rel = src_file.relative_to(v_dir)
                            dest = new_dir / rel
                            if not dest.exists():
                                dest.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(src_file, dest)
                    shutil.rmtree(v_dir)
                else:
                    v_dir.rename(new_dir)
                migrated += 1
            try:
                legacy_versions_dir.rmdir()
            except OSError:
                pass

        return migrated

    # ── Promotion Queue ──────────────────────────────────────────────

    PROMO_EXPIRY_DAYS = 30

    async def get_pending_promotions(self) -> list[dict[str, Any]]:
        """Return all pending promotion queue entries."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT * FROM skill_promotion_queue
                   WHERE status = 'pending'
                   ORDER BY created_at DESC"""
            )
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def execute_promotion(
        self, skill_name: str, reviewed_by: str = "admin"
    ) -> dict[str, Any] | None:
        """Execute a promotion: copy personal skill to shared, update DB."""
        import os
        import shutil
        from pathlib import Path

        DATA_ROOT = Path(os.environ.get("DATA_ROOT", "data")).resolve()

        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM skill_promotion_queue WHERE skill_name = ? AND status = 'pending'",
                (skill_name,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            queue_entry = dict(row)
            owner_id = queue_entry["original_owner_id"]

            # Find the personal skill path
            cursor = await conn.execute(
                "SELECT path FROM skills WHERE skill_name = ? AND source = 'personal' AND owner_id = ?",
                (skill_name, owner_id),
            )
            skill_row = await cursor.fetchone()
            if skill_row is None:
                await conn.execute(
                    "UPDATE skill_promotion_queue SET status = 'rejected', admin_review_comment = 'Source skill not found', reviewed_at = strftime('%s', 'now'), reviewed_by = ? WHERE skill_name = ? AND status = 'pending'",
                    (reviewed_by, skill_name),
                )
                await conn.commit()
                return None

            src_dir = Path(skill_row["path"])
            if not src_dir.exists():
                await conn.execute(
                    "UPDATE skill_promotion_queue SET status = 'rejected', admin_review_comment = 'Source directory not found', reviewed_at = strftime('%s', 'now'), reviewed_by = ? WHERE skill_name = ? AND status = 'pending'",
                    (reviewed_by, skill_name),
                )
                await conn.commit()
                return None

            # Copy to shared skills directory
            dest_dir = DATA_ROOT / "shared-skills" / skill_name
            if dest_dir.exists():
                await conn.execute(
                    "UPDATE skill_promotion_queue SET status = 'rejected', admin_review_comment = 'Shared skill already exists', reviewed_at = strftime('%s', 'now'), reviewed_by = ? WHERE skill_name = ? AND status = 'pending'",
                    (reviewed_by, skill_name),
                )
                await conn.commit()
                return None

            shutil.copytree(src_dir, dest_dir)

            # Update skill-meta.json with promotion info
            meta_path = dest_dir / "skill-meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                except (json.JSONDecodeError, OSError):
                    meta = {}
                meta["promoted_by"] = reviewed_by
                meta["promoted_at"] = "now"
                meta["original_owner"] = owner_id
                meta_path.write_text(json.dumps(meta, indent=2))

            # Register as shared skill in DB
            await conn.execute(
                """INSERT OR REPLACE INTO skills (skill_name, source, owner_id, description, category, tags, path, version, status, updated_at)
                   VALUES ('shared', ?, ?, ?, ?, ?, ?, 'active', strftime('%s', 'now'))""",
                (skill_name, owner_id, queue_entry.get("description", ""), "", "[]", str(dest_dir)),
            )

            # Mark queue entry as approved
            await conn.execute(
                "UPDATE skill_promotion_queue SET status = 'approved', reviewed_at = strftime('%s', 'now'), reviewed_by = ? WHERE skill_name = ? AND status = 'pending'",
                (reviewed_by, skill_name),
            )
            await conn.commit()

        return {
            "skill_name": skill_name,
            "source_path": str(src_dir),
            "dest_path": str(dest_dir),
            "status": "approved",
        }

    async def reject_promotion(
        self, skill_name: str, reason: str = "", reviewed_by: str = "admin"
    ) -> bool:
        """Reject a pending promotion."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "UPDATE skill_promotion_queue SET status = 'rejected', admin_review_comment = ?, reviewed_at = strftime('%s', 'now'), reviewed_by = ? WHERE skill_name = ? AND status = 'pending'",
                (reason, reviewed_by, skill_name),
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def cleanup_expired_promotions(self, days: int | None = None) -> int:
        """Auto-reject promotions older than the expiry window."""
        expiry = days or self.PROMO_EXPIRY_DAYS
        cutoff = expiry * 86400
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """UPDATE skill_promotion_queue
                   SET status = 'expired', reviewed_at = strftime('%s', 'now'), reviewed_by = 'system'
                   WHERE status = 'pending' AND created_at < strftime('%s', 'now') - ?""",
                (cutoff,),
            )
            await conn.commit()
            return cursor.rowcount

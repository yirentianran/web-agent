# Skill Database Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add database-backed skill registry, usage tracking, and version metadata to replace file-system scanning and enable search, filtering, and analytics.

**Architecture:** Three new SQLite tables (`skills`, `skill_usage`, `skill_versions`) layered on existing file-based storage. `src/skill_manager.py` as the manager class, integrated into startup migration and existing endpoints.

**Tech Stack:** SQLite (aiosqlite), FastAPI, existing `src/database.py` schema.

---

### Task 1: Database schema and SkillManager core

**Files:**
- Modify: `src/database.py` — add new table definitions
- Create: `src/skill_manager.py`
- Create: `tests/unit/test_skill_manager.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_skill_manager.py
import pytest
from pathlib import Path
from src.skill_manager import SkillManager
from src.database import Database

@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    db = Database(db_path=db_path)
    await db.init()
    yield db
    await db.close()

@pytest.mark.asyncio
async def test_register_skill(db):
    mgr = SkillManager(db=db)
    await mgr.register_skill(
        skill_name="test-skill",
        source="personal",
        owner_id="user1",
        description="A test skill",
        category="coding",
        tags=["python", "testing"],
    )
    skill = await mgr.get_skill("test-skill")
    assert skill is not None
    assert skill["skill_name"] == "test-skill"
    assert skill["owner_id"] == "user1"
    assert skill["category"] == "coding"
    assert "python" in skill["tags"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_skill_manager.py::test_register_skill -v`
Expected: ModuleNotFoundError (src/skill_manager.py doesn't exist)

- [ ] **Step 3: Add database schema to `src/database.py`**

In the `_CREATE_TABLES` string (after `generated_files` table), append:

```sql
-- Skills registry
CREATE TABLE IF NOT EXISTS skills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name  TEXT NOT NULL UNIQUE,
    source      TEXT NOT NULL DEFAULT 'personal',
    owner_id    TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'active',
    version     TEXT NOT NULL DEFAULT '',
    path        TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at  REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_skills_source ON skills(source);
CREATE INDEX IF NOT EXISTS idx_skills_owner ON skills(owner_id);
CREATE INDEX IF NOT EXISTS idx_skills_category ON skills(category);
CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status);

-- Skill usage tracking
CREATE TABLE IF NOT EXISTS skill_usage (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name     TEXT NOT NULL REFERENCES skills(skill_name),
    user_id        TEXT NOT NULL DEFAULT '',
    session_id     TEXT NOT NULL DEFAULT '',
    version_number INTEGER NOT NULL DEFAULT 0,
    action         TEXT NOT NULL DEFAULT 'use',
    created_at     REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_usage_skill ON skill_usage(skill_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_user ON skill_usage(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_session ON skill_usage(session_id);

-- Skill version metadata
CREATE TABLE IF NOT EXISTS skill_versions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name     TEXT NOT NULL REFERENCES skills(skill_name),
    version_number INTEGER NOT NULL,
    path           TEXT NOT NULL DEFAULT '',
    change_summary TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'pending',
    created_by     TEXT NOT NULL DEFAULT 'user',
    file_count     INTEGER NOT NULL DEFAULT 1,
    created_at     REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_versions_skill ON skill_versions(skill_name, version_number DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_versions_unique ON skill_versions(skill_name, version_number);
```

- [ ] **Step 4: Create `src/skill_manager.py` with core methods**

```python
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
        # Admin hard-delete from filesystem should use the existing
        # delete_skill/delete_shared_skill endpoints which know the owner.

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
            # Version dirs are {parent}/{skill_name}@vN/
            # Active dir is {parent}/{skill_name}/
            # Extract parent dir from version path:
            # e.g. /data/shared-skills/code-review@v2 → parent=/data/shared-skills
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
        """Rollback to most recent backup SKILL file.

        Finds SKILL_backup_vN.md files, restores the latest backup.
        """
        import shutil
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
            # Move current to a new backup
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_skill_manager.py -v`
Expected: All tests PASS

- [ ] **Step 6: Add tests for list_skills filtering**

```python
@pytest.mark.asyncio
async def test_list_skills_by_category(db):
    mgr = SkillManager(db=db)
    await mgr.register_skill("py-skill", source="personal", owner_id="u1", category="coding")
    await mgr.register_skill("data-skill", source="personal", owner_id="u1", category="data")
    coding = await mgr.list_skills(category="coding")
    assert len(coding) == 1
    assert coding[0]["skill_name"] == "py-skill"

@pytest.mark.asyncio
async def test_list_skills_by_tag(db):
    mgr = SkillManager(db=db)
    await mgr.register_skill("py-skill", source="personal", owner_id="u1", tags=["python", "testing"])
    await mgr.register_skill("js-skill", source="personal", owner_id="u1", tags=["javascript"])
    result = await mgr.list_skills(tag="python")
    assert len(result) == 1
    assert result[0]["skill_name"] == "py-skill"

@pytest.mark.asyncio
async def test_record_usage_and_stats(db):
    mgr = SkillManager(db=db)
    await mgr.register_skill("test-skill", source="shared", owner_id="")
    await mgr.record_usage("test-skill", user_id="u1", session_id="s1", version_number=2)
    await mgr.record_usage("test-skill", user_id="u2", session_id="s2", version_number=2)
    await mgr.record_usage("test-skill", user_id="u1", session_id="s3", version_number=1)
    stats = await mgr.get_usage_stats("test-skill")
    assert stats["total_uses"] == 3
    assert stats["unique_users"] == 2
    assert len(stats["version_breakdown"]) == 2
    assert stats["version_breakdown"][0]["version"] == 2

@pytest.mark.asyncio
async def test_version_lifecycle(db, tmp_path):
    mgr = SkillManager(db=db)
    # Create skill directory with SKILL.md
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Initial version")

    await mgr.register_skill("test-skill", source="shared", owner_id="", path=str(skill_dir))

    # Create version 1 directory
    v1_dir = tmp_path / "test-skill@v1"
    v1_dir.mkdir()
    (v1_dir / "SKILL.md").write_text("# Version 1")
    await mgr.record_version("test-skill", 1, path=str(v1_dir), change_summary="Initial", created_by="upload")

    # Create version 2 directory
    v2_dir = tmp_path / "test-skill@v2"
    v2_dir.mkdir()
    (v2_dir / "SKILL.md").write_text("# Version 2")
    await mgr.record_version("test-skill", 2, path=str(v2_dir), change_summary="Updated", created_by="agent")

    versions = await mgr.list_versions("test-skill")
    assert len(versions) == 2
    assert versions[0]["version_number"] == 2

    result = await mgr.activate_version("test-skill", 2)
    assert result is not None
    assert result["activated"] is True
    assert (skill_dir / "SKILL.md").read_text() == "# Version 2"
    skill = await mgr.get_skill("test-skill")
    assert skill["version"] == "v2"
```

- [ ] **Step 7: Run tests and verify they pass**

Run: `uv run pytest tests/unit/test_skill_manager.py -v`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/database.py src/skill_manager.py tests/unit/test_skill_manager.py
git commit -m "feat: add skill DB schema and SkillManager core with tests
- Add skills, skill_usage, skill_versions tables to database schema
- Implement register, list, update, delete, usage tracking, and version management
- Unit tests for all CRUD operations, filtering, and stats"
```

---

### Task 2: Startup migration — filesystem → DB

**Files:**
- Modify: `src/skill_manager.py` — add `migrate_from_filesystem` method
- Modify: `main_server.py` — add migration call in `startup()`
- Create: `tests/integration/test_skill_migration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_skill_migration.py
import pytest
import json
from pathlib import Path
from src.skill_manager import SkillManager
from src.database import Database

@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    db = Database(db_path=db_path)
    await db.init()
    yield db
    await db.close()

@pytest.fixture
def data_root(tmp_path):
    """Create a fake data root with some skills."""
    root = tmp_path / "data"
    shared = root / "shared-skills" / "code-review"
    shared.mkdir(parents=True)
    (shared / "SKILL.md").write_text("# Code Review Skill")
    meta = shared / "skill-meta.json"
    meta.write_text(json.dumps({"owner": "admin", "source": "shared"}))

    personal = root / "users" / "user1" / "workspace" / ".claude" / "skills" / "my-skill"
    personal.mkdir(parents=True)
    (personal / "SKILL.md").write_text("# My Skill")

    return root

@pytest.mark.asyncio
async def test_migrate_from_filesystem(db, data_root, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(data_root))
    mgr = SkillManager(db=db)
    result = await mgr.migrate_from_filesystem()
    assert result["registered"] == 2
    skill = await mgr.get_skill("code-review")
    assert skill is not None
    assert skill["source"] == "shared"
    assert skill["owner_id"] == "admin"
    personal = await mgr.get_skill("my-skill")
    assert personal is not None
    assert personal["source"] == "personal"
    assert personal["owner_id"] == "user1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_skill_migration.py -v`
Expected: AttributeError (migrate_from_filesystem doesn't exist)

- [ ] **Step 3: Add `migrate_from_filesystem` to `src/skill_manager.py`**

Append to SkillManager class:

```python
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
                # Skip historical version directories (@vN pattern)
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
                # Migrate legacy versions in this directory
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
                    # Skip historical version directories
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
                    # Merge: copy files, skip conflicts
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
            # Remove versions dir if empty
            try:
                legacy_versions_dir.rmdir()
            except OSError:
                pass  # Not empty, leave as-is

        return migrated
```

- [ ] **Step 4: Add migration call to `main_server.py` startup()**

In `main_server.py`, after the existing feedback JSONL migration block (around line 5846), add:

```python
        # Migrate existing skills from filesystem to DB
        try:
            from src.skill_manager import SkillManager

            skill_mgr = SkillManager(db=_db)
            result = await skill_mgr.migrate_from_filesystem()
            if result["registered"] > 0:
                logger.info(
                    "Skill migration: %d registered, %d versions migrated",
                    result["registered"],
                    result["versions_migrated"],
                )
        except Exception:
            logger.exception("Skill DB migration failed")
```

- [ ] **Step 5: Update `load_skills()` to skip `@vN` directories**

In `main_server.py`, find `load_skills()` (line ~371). Add skip filter after the existing `is_dir()` check for both shared and personal loops:

```python
# Shared skills loop (line ~382)
if not skill_dir.is_dir() or skill_dir.is_symlink():
    continue
if "@v" in skill_dir.name:
    continue  # skip historical version directories
```

```python
# Personal skills loop (line ~399)
if not skill_dir.is_dir() or skill_dir.is_symlink() or (skill_dir / ".shared_skill_source").exists():
    continue
if "@v" in skill_dir.name:
    continue  # skip historical version directories
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_skill_migration.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/skill_manager.py main_server.py tests/integration/test_skill_migration.py
git commit -m "feat: add startup migration from filesystem to skill DB
- Add migrate_from_filesystem to scan and register all existing skills
- Migrate legacy nested versions to flat @vN directories
- Update load_skills() to skip @vN version directories
- Integration test with fake data root"
```

---

### Task 3: Integrate SkillManager into existing endpoints

**Files:**
- Modify: `main_server.py` — wire SkillManager into upload, activate, evolve endpoints
- Modify: `main_server.py` — add `_skill_manager` global variable

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_skill_manager.py — append to existing file
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_register_on_upload(tmp_path):
    """When a skill is uploaded, it gets registered in DB."""
    db_path = tmp_path / "test.db"
    db = Database(db_path=db_path)
    await db.init()
    mgr = SkillManager(db=db)

    # Simulate upload registration
    await mgr.register_skill(
        skill_name="new-skill",
        source="personal",
        owner_id="user1",
        description="Uploaded via API",
    )
    skill = await mgr.get_skill("new-skill")
    assert skill is not None
    assert skill["source"] == "personal"
    assert skill["status"] == "active"

    await db.close()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_skill_manager.py::test_register_on_upload -v`
Expected: PASS (register_skill already exists from Task 1)

- [ ] **Step 3: Add `_skill_manager` global in `main_server.py`**

Near line 137 (after `_db` and `_audit_logger` declarations), add:

```python
_skill_manager: Any = None  # SkillManager, initialized at startup if DB available
```

- [ ] **Step 4: Initialize `_skill_manager` in `main_server.py` startup()**

After the skill migration block (added in Task 2), add:

```python
        # Initialize SkillManager for runtime use
        _skill_manager = SkillManager(db=_db)
```

- [ ] **Step 5: Wire into upload_skill_files endpoint**

In `main_server.py`, find `upload_skill_files` (line ~4028). After the ZIP extraction/skill file writing succeeds, add:

```python
    # Register skill in DB
    if _skill_manager is not None:
        try:
            await _skill_manager.register_skill(
                skill_name=skill_name,
                source="personal",
                owner_id=current_user,
                description="",
                path=str(skill_dir),
            )
        except Exception:
            logger.exception("Failed to register skill in DB: %s", skill_name)
```

Insert after the skill files are written (find the line that writes the last file or returns success).

- [ ] **Step 6: Wire into upload_shared_skill endpoint**

In `main_server.py`, find `upload_shared_skill` (line ~4082). After the skill files are extracted, add:

```python
    # Register skill in DB
    if _skill_manager is not None:
        try:
            await _skill_manager.register_skill(
                skill_name=skill_name,
                source="shared",
                owner_id="admin",
                description="",
                path=str(skill_dir),
            )
        except Exception:
            logger.exception("Failed to register shared skill in DB: %s", skill_name)
```

- [ ] **Step 7: Wire into activate_skill_version endpoint**

In `main_server.py`, find `activate_skill_version` (line ~4715). After the existing `mgr.db_activate_version` call succeeds, add:

```python
    if _skill_manager is not None and result:
        try:
            await _skill_manager.activate_version(skill_name, req.version_number)
        except Exception:
            logger.exception("Failed to activate version in DB: %s", skill_name)
```

- [ ] **Step 8: Commit**

```bash
git add main_server.py
git commit -m "feat: wire SkillManager into upload and version endpoints
- Register skills in DB on personal and shared upload
- Activate version in DB when version is activated
- Initialize _skill_manager global at startup"
```

---

### Task 4: New REST API endpoints

**Files:**
- Modify: `main_server.py` — add new endpoints
- Modify: `src/models.py` — add request/response models
- Create: `tests/unit/test_skills_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_skills_api.py
import pytest
from fastapi.testclient import TestClient

def test_list_skills_empty(app_client):
    """GET /api/skills returns empty list when no skills registered."""
    resp = app_client.get("/api/skills")
    assert resp.status_code == 200
    data = resp.json()
    assert data["skills"] == []

def test_list_skills_with_filter(app_client):
    """GET /api/skills?category=coding filters correctly."""
    # First register a skill via the API (or direct DB insert)
    # ...
    resp = app_client.get("/api/skills?category=coding")
    assert resp.status_code == 200
    data = resp.json()
    assert all(s["category"] == "coding" for s in data["skills"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_skills_api.py::test_list_skills_empty -v`
Expected: 404 (endpoint doesn't exist)

- [ ] **Step 3: Add request/response models to `src/models.py`**

```python
class SkillUpdateRequest(BaseModel):
    description: str = ""
    category: str = ""
    tags: list[str] = []
    status: str = "active"


class SkillsListResponse(BaseModel):
    skills: list[dict[str, Any]]
    total: int
```

- [ ] **Step 4: Add `GET /api/skills` endpoint**

```python
@app.get("/api/skills", response_model=SkillsListResponse)
async def list_skills(
    source: str | None = None,
    category: str | None = None,
    tag: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    current_user: str = Depends(get_current_user),
) -> SkillsListResponse:
    """List all skills with optional filters."""
    if _skill_manager is None:
        return SkillsListResponse(skills=[], total=0)
    skills = await _skill_manager.list_skills(
        source=source, category=category, tag=tag, status=status, owner=owner,
    )
    return SkillsListResponse(skills=skills, total=len(skills))
```

- [ ] **Step 5: Add `GET /api/skills/{skill_name}/usage` endpoint**

```python
@app.get("/api/skills/{skill_name}/usage")
async def get_skill_usage(
    skill_name: str,
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Get usage statistics for a skill."""
    if _skill_manager is None:
        return {"skill_name": skill_name, "total_uses": 0}
    return await _skill_manager.get_usage_stats(skill_name)
```

- [ ] **Step 6: Add `POST /api/skills/{skill_name}/usage` endpoint**

```python
class UsageRecord(BaseModel):
    user_id: str = ""
    session_id: str = ""
    version_number: int = 0
    action: str = "use"


@app.post("/api/skills/{skill_name}/usage")
async def record_skill_usage(
    skill_name: str,
    req: UsageRecord,
) -> dict[str, str]:
    """Record a skill usage event."""
    if _skill_manager is not None:
        await _skill_manager.record_usage(
            skill_name,
            user_id=req.user_id,
            session_id=req.session_id,
            version_number=req.version_number,
            action=req.action,
        )
    return {"status": "ok"}
```

- [ ] **Step 7: Add `PUT /api/admin/skills/{skill_name}/meta` endpoint**

```python
@app.put("/api/admin/skills/{skill_name}/meta")
async def update_skill_metadata(
    skill_name: str,
    req: SkillUpdateRequest,
    current_user: str = Depends(require_admin),
) -> dict[str, Any]:
    """Update skill category, tags, description, status. Admin only."""
    if _skill_manager is None:
        return JSONResponse({"error": "Skill DB not available"}, status_code=503)
    skill = await _skill_manager.get_skill(skill_name)
    if skill is None:
        return JSONResponse({"error": "skill not found"}, status_code=404)
    await _skill_manager.update_skill_meta(
        skill_name,
        description=req.description,
        category=req.category,
        tags=req.tags,
        status=req.status,
    )
    return {"status": "ok"}
```

- [ ] **Step 8: Add `GET /api/admin/skills/manage` endpoint**

```python
@app.get("/api/admin/skills/manage")
async def admin_skills_dashboard(
    current_user: str = Depends(require_admin),
) -> dict[str, Any]:
    """Admin dashboard: all skills with usage stats and top skills."""
    if _skill_manager is None:
        return {"skills": [], "top_skills": [], "total": 0}
    skills = await _skill_manager.list_skills()
    top_skills = await _skill_manager.get_top_skills(limit=10)
    return {"skills": skills, "top_skills": top_skills, "total": len(skills)}
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_skills_api.py -v`
Expected: All tests PASS

- [ ] **Step 10: Commit**

```bash
git add src/models.py main_server.py tests/unit/test_skills_api.py
git commit -m "feat: add new skill REST endpoints
- GET /api/skills with filtering (category, tag, source, status, owner)
- GET/POST /api/skills/{name}/usage for usage stats and recording
- PUT /api/admin/skills/{name}/meta for admin metadata updates
- GET /api/admin/skills/manage for admin dashboard"
```

---

### Task 5: Usage recording in agent subprocess

**Files:**
- Modify: `agent_server.py` — record usage when skills are loaded

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_skill_manager.py — append
@pytest.mark.asyncio
async def test_record_usage_fire_and_forget(db):
    """record_usage never raises, even with bad data."""
    mgr = SkillManager(db=db)
    # Should not raise even if skill doesn't exist
    await mgr.record_usage("nonexistent-skill", user_id="u1", session_id="s1")
    # Should still work
    await mgr.record_usage("test-skill", version_number=-1, action="invalid")
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_skill_manager.py::test_record_usage_fire_and_forget -v`
Expected: PASS (record_usage already has try/except)

- [ ] **Step 3: Add usage recording in `agent_server.py`**

Find where skills are loaded (the code that calls `load_skills` or reads skill files). After each skill is successfully loaded, add:

```python
# Record usage in DB (fire-and-forget)
if skill_manager is not None:
    try:
        await skill_manager.record_usage(
            skill_name=skill_name,
            user_id=user_id,
            session_id=session_id,
            version_number=0,  # Will be updated when version is resolved
            action="load",
        )
    except Exception:
        pass
```

Note: `agent_server.py` uses a separate process. The skill_manager needs to be initialized with the same DB path. Check how `_db` is shared and add the initialization accordingly. If agent_server can't access the same DB connection, it should use an HTTP call to `POST /api/skills/{name}/usage` instead.

- [ ] **Step 4: Commit**

```bash
git add agent_server.py
git commit -m "feat: record skill usage when agent loads skills
- Fire-and-forget usage recording in agent subprocess
- Tracks user, session, and action type per skill load"
```

---

### Task 6: Extend existing analytics endpoint

**Files:**
- Modify: `main_server.py` — extend `get_skill_analytics` to include usage data

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_skills_api.py — append
@pytest.mark.asyncio
async def test_analytics_includes_usage(db):
    """GET /api/skills/{name}/analytics includes usage data."""
    mgr = SkillManager(db=db)
    await mgr.register_skill("test-skill", source="shared", owner_id="")
    await mgr.record_usage("test-skill", user_id="u1", action="load")
    await mgr.record_usage("test-skill", user_id="u2", action="use")
    stats = await mgr.get_usage_stats("test-skill")
    assert stats["total_uses"] == 2
    assert stats["unique_users"] == 2
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_skills_api.py::test_analytics_includes_usage -v`
Expected: PASS

- [ ] **Step 3: Extend `get_skill_analytics` endpoint**

In `main_server.py`, find `get_skill_analytics` (line ~4570). After the existing feedback analytics computation, add usage data:

```python
    # Add usage data if SkillManager is available
    usage_data = {}
    if _skill_manager is not None:
        try:
            usage_data = await _skill_manager.get_usage_stats(skill_name)
        except Exception:
            pass
    result.update(usage_data)
```

- [ ] **Step 4: Commit**

```bash
git add main_server.py
git commit -m "feat: extend skill analytics endpoint with usage data
- Merge usage stats (total_uses, unique_users, version breakdown)
  into existing analytics response"
```

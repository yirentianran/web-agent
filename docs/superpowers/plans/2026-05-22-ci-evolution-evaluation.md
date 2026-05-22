# CI Evolution Evaluation & Admin Dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace keyword-based auto_evolve pipeline with ECC-inspired session-learner that analyzes full conversation context via Haiku, plus add evaluation/rollback and admin dashboard.

**Architecture:** Session end triggers `session_learner.analyze_session()` → queries `messages` table for full conversation → calls Haiku for analysis → confidence ≥7 auto-applies (writes SKILL.md + skill-meta.json, registers in DB via SkillManager, bumps shared-skills generation, logs to `evolution_log`). Learned skills go directly into `shared-skills/{name}/` with `source='learned'` — immediately discoverable by `load_skills()` via `iterdir()` and synced to user workspaces via `_sync_shared_skills()`. Daily `_eval_snapshot_loop` computes composite scores, detects degradation, transitions to under_review with 48h auto-rollback. Admin dashboard at `/dashboard/evolution` shows overview table and detail page with charts, diff, and rollback controls.

**Tech Stack:** Python (FastAPI, aiosqlite, httpx), React (Recharts, Monaco Editor), SQLite

---

## File Structure

| File | Role |
|------|------|
| `src/database.py` | Add `evolution_log` + `skill_eval_snapshots` tables |
| `src/evolution_log.py` | CRUD for both tables |
| `src/session_learner.py` | Session-end analysis: DB query → Haiku prompt → confidence-based apply → SkillManager registration + gen bump |
| `src/evolution_evaluator.py` | Daily snapshot + composite scoring + degradation detection |
| `src/evolution_rollback.py` | Rollback state machine, skill version restore |
| `main_server.py` | Session-end hook, 4 admin API endpoints |
| `src/collective_intelligence.py` | Add `_eval_snapshot_loop()`, deprecate `_auto_evolve_loop()` |
| `tests/unit/test_session_learner.py` | Session learner tests |
| `tests/unit/test_evolution_evaluator.py` | Evaluator + rollback tests |
| `tests/unit/test_evolution_api.py` | Admin API integration tests |
| `frontend/src/hooks/useEvolutionApi.ts` | Data fetching hook |
| `frontend/src/pages/EvolutionPage.tsx` | Overview table + detail view |
| `frontend/src/pages/evolution/` | Sub-components: OverviewTable, ScoreTrendChart, SignalBreakdown, VersionDiff, RollbackTimeline |
| `frontend/src/App.tsx` | Add `/dashboard/evolution` route |

---

### Task 1: Database — Add evolution_log and skill_eval_snapshots tables

**Files:**
- Modify: `src/database.py`

- [ ] **Step 1: Add table creation to `src/database.py`**

In the `_init_tables` method (or equivalent), add after the existing `CREATE TABLE IF NOT EXISTS` blocks:

```python
# Phase 5: Evolution evaluation tables
cursor = await conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='evolution_log'"
)
if not await cursor.fetchone():
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS evolution_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_name TEXT NOT NULL,
            from_version TEXT NOT NULL,
            to_version TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'session_learner',
            evolve_reason TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at INTEGER NOT NULL,
            reviewed_at INTEGER,
            reviewed_by TEXT,
            review_decision TEXT,
            auto_rollback_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS skill_eval_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            evolution_log_id INTEGER NOT NULL REFERENCES evolution_log(id),
            snapshot_date TEXT NOT NULL,
            usage_count INTEGER DEFAULT 0,
            unique_users INTEGER DEFAULT 0,
            avg_rating REAL,
            session_success_rate REAL,
            composite_score REAL,
            created_at INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_evo_log_status ON evolution_log(status);
        CREATE INDEX IF NOT EXISTS idx_evo_log_skill ON evolution_log(skill_name);
        CREATE INDEX IF NOT EXISTS idx_eval_snap_log ON skill_eval_snapshots(evolution_log_id);
    """)
```

- [ ] **Step 2: Commit**

```bash
git add src/database.py
git commit -m "feat: add evolution_log and skill_eval_snapshots tables"
```

---

### Task 2: Backend — evolution_log.py CRUD module

**Files:**
- Create: `src/evolution_log.py`
- Modify: `tests/unit/test_evolution_evaluator.py` (create test file, start with log CRUD tests)

- [ ] **Step 1: Write the CRUD module**

```python
"""CRUD for evolution_log and skill_eval_snapshots tables."""
from __future__ import annotations

import time
from typing import Any

if __name__ != "__main__":
    from src.database import Database


class EvolutionLogStore:
    """CRUD for evolution tracking tables."""

    def __init__(self, db: "Database") -> None:
        self.db = db

    async def create_log(
        self,
        skill_name: str,
        from_version: str,
        to_version: str,
        *,
        source: str = "session_learner",
        evolve_reason: str = "",
    ) -> dict[str, Any]:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """INSERT INTO evolution_log
                   (skill_name, from_version, to_version, source, evolve_reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (skill_name, from_version, to_version, source, evolve_reason, int(time.time())),
            )
            return {"id": cursor.lastrowid}

    async def update_status(
        self, log_id: int, status: str, **extra: Any
    ) -> None:
        sets = ["status = ?"]
        params: list[Any] = [status]
        for key, val in extra.items():
            sets.append(f"{key} = ?")
            params.append(val)
        params.append(log_id)
        async with self.db.connection() as conn:
            await conn.execute(
                f"UPDATE evolution_log SET {', '.join(sets)} WHERE id = ?",
                params,
            )

    async def get_log(self, log_id: int) -> dict[str, Any] | None:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM evolution_log WHERE id = ?", (log_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def list_logs(
        self,
        *,
        status: str | None = None,
        skill_name: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        where = []
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if skill_name:
            where.append("skill_name = ?")
            params.append(skill_name)

        clause = f"WHERE {' AND '.join(where)}" if where else ""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT COUNT(*) FROM evolution_log {clause}", params
            )
            row = await cursor.fetchone()
            total = row[0] if row else 0

            offset = (page - 1) * page_size
            cursor = await conn.execute(
                f"""SELECT * FROM evolution_log {clause}
                    ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                params + [page_size, offset],
            )
            rows = await cursor.fetchall()
            return {
                "items": [dict(r) for r in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
            }

    # ── Snapshots ────────────────────────────────────────────────

    async def create_snapshot(
        self,
        evolution_log_id: int,
        snapshot_date: str,
        usage_count: int,
        unique_users: int,
        avg_rating: float,
        session_success_rate: float,
        composite_score: float,
    ) -> int:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """INSERT INTO skill_eval_snapshots
                   (evolution_log_id, snapshot_date, usage_count, unique_users,
                    avg_rating, session_success_rate, composite_score, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (evolution_log_id, snapshot_date, usage_count, unique_users,
                 avg_rating, session_success_rate, composite_score, int(time.time())),
            )
            return cursor.lastrowid

    async def get_snapshots(self, evolution_log_id: int) -> list[dict[str, Any]]:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT * FROM skill_eval_snapshots
                   WHERE evolution_log_id = ?
                   ORDER BY snapshot_date ASC""",
                (evolution_log_id,),
            )
            return [dict(r) for r in await cursor.fetchall()]

    async def get_last_snapshots(
        self, evolution_log_id: int, count: int = 7
    ) -> list[dict[str, Any]]:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT * FROM skill_eval_snapshots
                   WHERE evolution_log_id = ?
                   ORDER BY snapshot_date DESC LIMIT ?""",
                (evolution_log_id, count),
            )
            rows = await cursor.fetchall()
            rows.reverse()
            return [dict(r) for r in rows]

    async def get_active_evolutions(self) -> list[dict[str, Any]]:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT * FROM evolution_log
                   WHERE status IN ('active', 'under_review')
                   ORDER BY created_at""",
            )
            return [dict(r) for r in await cursor.fetchall()]

    async def get_expired_reviews(self) -> list[dict[str, Any]]:
        now = int(time.time())
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT * FROM evolution_log
                   WHERE status = 'under_review'
                   AND auto_rollback_at IS NOT NULL
                   AND auto_rollback_at < ?""",
                (now,),
            )
            return [dict(r) for r in await cursor.fetchall()]
```

- [ ] **Step 2: Write tests**

```python
# tests/unit/test_evolution_evaluator.py
"""Tests for evolution log CRUD and evaluator."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_create_log_returns_id(db):
    from src.evolution_log import EvolutionLogStore
    store = EvolutionLogStore(db)
    result = await store.create_log("test-skill", "1.0", "1.1", evolve_reason="test")
    assert result["id"] is not None
    assert isinstance(result["id"], int)


@pytest.mark.asyncio
async def test_list_logs_filters_by_status(db):
    from src.evolution_log import EvolutionLogStore
    store = EvolutionLogStore(db)
    await store.create_log("skill-a", "1.0", "1.1")
    await store.create_log("skill-b", "2.0", "2.1")
    result = await store.list_logs(status="active")
    assert result["total"] == 2


@pytest.mark.asyncio
async def test_list_logs_empty(db):
    from src.evolution_log import EvolutionLogStore
    store = EvolutionLogStore(db)
    result = await store.list_logs(status="rolled_back")
    assert result["total"] == 0
    assert result["items"] == []


@pytest.mark.asyncio
async def test_create_and_get_snapshots(db):
    from src.evolution_log import EvolutionLogStore
    store = EvolutionLogStore(db)
    r = await store.create_log("s", "1.0", "1.1")
    log_id = r["id"]
    await store.create_snapshot(log_id, "2026-05-22", 10, 3, 4.0, 0.85, 0.72)
    await store.create_snapshot(log_id, "2026-05-23", 8, 2, 3.5, 0.80, 0.60)
    snaps = await store.get_snapshots(log_id)
    assert len(snaps) == 2
    assert snaps[0]["composite_score"] == 0.72


@pytest.mark.asyncio
async def test_get_last_snapshots_returns_correct_count(db):
    from src.evolution_log import EvolutionLogStore
    store = EvolutionLogStore(db)
    r = await store.create_log("s", "1.0", "1.1")
    for i in range(10):
        await store.create_snapshot(r["id"], f"2026-05-{22+i:02d}", 5, 2, 3.0, 0.7, 0.5)
    snaps = await store.get_last_snapshots(r["id"], count=7)
    assert len(snaps) == 7


@pytest.mark.asyncio
async def test_get_active_evolutions_filters_rolled_back(db):
    from src.evolution_log import EvolutionLogStore
    store = EvolutionLogStore(db)
    r = await store.create_log("s", "1.0", "1.1")
    await store.update_status(r["id"], "rolled_back")
    active = await store.get_active_evolutions()
    assert len(active) == 0


@pytest.mark.asyncio
async def test_get_expired_reviews(db):
    import time
    from src.evolution_log import EvolutionLogStore
    store = EvolutionLogStore(db)
    r = await store.create_log("s", "1.0", "1.1")
    await store.update_status(r["id"], "under_review", auto_rollback_at=int(time.time()) - 3600)
    expired = await store.get_expired_reviews()
    assert len(expired) == 1
    assert expired[0]["id"] == r["id"]
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/unit/test_evolution_evaluator.py -v
```

Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add src/evolution_log.py tests/unit/test_evolution_evaluator.py
git commit -m "feat: add evolution_log CRUD module"
```

---

### Task 3: Backend — evolution_evaluator.py (daily snapshot + degradation)

**Files:**
- Create: `src/evolution_evaluator.py`
- Modify: `tests/unit/test_evolution_evaluator.py` (append evaluator tests)

- [ ] **Step 1: Write the evaluator**

```python
"""Daily evolution evaluation: snapshot generation and degradation detection."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

if __name__ != "__main__":
    from src.database import Database
    from src.evolution_log import EvolutionLogStore

logger = logging.getLogger(__name__)

# Weights for composite score
W_RATING = 0.4
W_USAGE = 0.3
W_SUCCESS = 0.3


class EvolutionEvaluator:
    """Generates daily snapshots and detects degradation."""

    def __init__(self, db: "Database") -> None:
        self.db = db
        self.store = EvolutionLogStore(db)

    async def run_daily_eval(self) -> None:
        """Run the daily evaluation cycle (called by CI scheduler at 02:00)."""
        active = await self.store.get_active_evolutions()
        today = datetime.now(UTC).strftime("%Y-%m-%d")

        for log in active:
            snap = await self._compute_snapshot(log, today)
            await self.store.create_snapshot(**snap)

            last_7 = await self.store.get_last_snapshots(log["id"], 7)
            if len(last_7) < 7:
                continue

            baseline = self._baseline_score(log["skill_name"], log["created_at"])
            if all(s["composite_score"] < baseline for s in last_7):
                from datetime import UTC as _utc
                import time
                rollback_at = int(time.time()) + 48 * 3600
                await self.store.update_status(
                    log["id"], "under_review", auto_rollback_at=rollback_at
                )
                logger.warning(
                    "Skill %s (log %d) degraded — under_review, auto-rollback at %d",
                    log["skill_name"], log["id"], rollback_at,
                )

    async def _compute_snapshot(
        self, log: dict, date_str: str
    ) -> dict:
        """Compute a single day's snapshot for an evolution."""
        skill_name = log["skill_name"]
        date_start = f"{date_str}T00:00:00"
        date_end = f"{date_str}T23:59:59"

        async with self.db.connection() as conn:
            # Usage count for this skill since evolution
            cursor = await conn.execute(
                """SELECT COUNT(*) FROM skill_usage
                   WHERE skill_name = ? AND created_at >= ?""",
                (skill_name, log["created_at"]),
            )
            row = await cursor.fetchone()
            usage_total = row[0] if row else 0

            # Daily unique users
            cursor = await conn.execute(
                """SELECT COUNT(DISTINCT user_id) FROM skill_usage
                   WHERE skill_name = ?
                   AND created_at >= strftime('%s', ?)
                   AND created_at <= strftime('%s', ?)""",
                (skill_name, date_start, date_end),
            )
            row = await cursor.fetchone()
            unique_users = row[0] if row else 0

            # Avg rating from feedback since evolution
            cursor = await conn.execute(
                """SELECT AVG(rating) FROM skill_feedback
                   WHERE skill_name = ?""",
                (skill_name,),
            )
            row = await cursor.fetchone()
            avg_rating = row[0] if row and row[0] else 0.0

            # Session success rate (sessions NOT ending in error)
            cursor = await conn.execute(
                """SELECT COUNT(*) FROM sessions
                   WHERE session_id IN (
                       SELECT DISTINCT session_id FROM skill_usage
                       WHERE skill_name = ? AND created_at >= ?
                   )""",
                (skill_name, log["created_at"]),
            )
            row = await cursor.fetchone()
            total_sessions = row[0] if row else 1

            # Sessions with this skill that had errors
            cursor = await conn.execute(
                """SELECT COUNT(DISTINCT m.session_id) FROM messages m
                   JOIN skill_usage su ON m.session_id = su.session_id
                   WHERE su.skill_name = ?
                   AND su.created_at >= ?
                   AND m.type IN ('error', 'system')
                   AND m.subtype = 'error'""",
                (skill_name, log["created_at"]),
            )
            row = await cursor.fetchone()
            error_sessions = row[0] if row else 0

            session_success_rate = 1.0 - (error_sessions / max(total_sessions, 1))

        # Composite score
        usage_trend_ratio = min(usage_total / max(self._baseline_usage(skill_name, log["created_at"]), 1), 1.0)
        composite = (
            W_RATING * (avg_rating / 5.0)
            + W_USAGE * usage_trend_ratio
            + W_SUCCESS * session_success_rate
        )

        return {
            "evolution_log_id": log["id"],
            "snapshot_date": date_str,
            "usage_count": usage_total,
            "unique_users": unique_users,
            "avg_rating": round(avg_rating, 2),
            "session_success_rate": round(session_success_rate, 2),
            "composite_score": round(composite, 4),
        }

    def _baseline_score(self, skill_name: str, evolved_at: int) -> float:
        """Compute baseline composite score from 7 days before evolution.
        Fixed at 0.6 default when insufficient pre-evolution data exists."""
        # For initial implementation, use a fixed baseline of 0.6.
        # This can be enhanced later to compute from pre-evolution snapshots.
        return 0.6

    def _baseline_usage(self, skill_name: str, evolved_at: int) -> int:
        """Baseline daily usage before evolution. Default 5."""
        return 5
```

- [ ] **Step 2: Add evaluator tests to the test file**

```python
# Append to tests/unit/test_evolution_evaluator.py

from unittest.mock import AsyncMock, MagicMock, patch


class TestCompositeScore:
    def test_score_calculation(self):
        """composite = 0.4*(4.0/5.0) + 0.3*1.0 + 0.3*0.9 = 0.32+0.3+0.27 = 0.89"""
        from src.evolution_evaluator import W_RATING, W_USAGE, W_SUCCESS
        rating = 4.0
        usage_ratio = 1.0
        success_rate = 0.9
        score = W_RATING * (rating / 5.0) + W_USAGE * usage_ratio + W_SUCCESS * success_rate
        assert round(score, 4) == 0.89

    def test_score_calculation_low(self):
        from src.evolution_evaluator import W_RATING, W_USAGE, W_SUCCESS
        rating = 1.0
        usage_ratio = 0.2
        success_rate = 0.5
        score = W_RATING * (rating / 5.0) + W_USAGE * usage_ratio + W_SUCCESS * success_rate
        assert round(score, 4) == 0.29


@pytest.mark.asyncio
async def test_daily_eval_creates_snapshots(db):
    import time
    from src.evolution_log import EvolutionLogStore
    from src.evolution_evaluator import EvolutionEvaluator

    store = EvolutionLogStore(db)
    r = await store.create_log("skill-x", "1.0", "1.1")
    evaluator = EvolutionEvaluator(db)
    await evaluator.run_daily_eval()
    snaps = await store.get_snapshots(r["id"])
    assert len(snaps) == 1
    assert "composite_score" in snaps[0]


@pytest.mark.asyncio
async def test_degradation_triggers_under_review(db):
    import time
    from src.evolution_log import EvolutionLogStore
    from src.evolution_evaluator import EvolutionEvaluator

    store = EvolutionLogStore(db)
    r = await store.create_log("skill-y", "1.0", "1.1")

    # Insert 7 low-score snapshots (all below 0.6 baseline)
    for i in range(7):
        await store.create_snapshot(
            r["id"], f"2026-05-{15+i:02d}",
            usage_count=1, unique_users=1,
            avg_rating=1.0, session_success_rate=0.3, composite_score=0.2,
        )

    evaluator = EvolutionEvaluator(db)
    await evaluator.run_daily_eval()

    log = await store.get_log(r["id"])
    assert log["status"] == "under_review"
    assert log["auto_rollback_at"] is not None
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/unit/test_evolution_evaluator.py -v
```

Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add src/evolution_evaluator.py tests/unit/test_evolution_evaluator.py
git commit -m "feat: add evolution evaluator with daily snapshot and degradation detection"
```

---

### Task 4: Backend — evolution_rollback.py (rollback state machine)

**Files:**
- Create: `src/evolution_rollback.py`

- [ ] **Step 1: Write the rollback module**

```python
"""Rollback state machine: restore previous skill version on degradation."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

if __name__ != "__main__":
    from src.database import Database
    from src.evolution_log import EvolutionLogStore

logger = logging.getLogger(__name__)


class EvolutionRollback:
    """Executes skill version rollback when evolution degrades."""

    def __init__(self, db: "Database", data_root: Path) -> None:
        self.db = db
        self.data_root = data_root
        self.store = EvolutionLogStore(db)

    async def execute_rollback(self, log_id: int, reason: str = "auto-rollback") -> bool:
        """Roll back a skill to its previous version.

        Returns True on success, False if rollback not possible.
        """
        log = await self.store.get_log(log_id)
        if not log:
            logger.error("Rollback failed: evolution_log %d not found", log_id)
            return False

        skill_name = log["skill_name"]
        from_version = log["from_version"]
        to_version = log["to_version"]

        skill_dir = self.data_root / "shared-skills" / skill_name
        skill_file = skill_dir / "SKILL.md"
        backup_file = skill_dir / f"SKILL_backup_v{to_version}.md"

        if not skill_file.exists():
            logger.error("Rollback failed: SKILL.md not found for %s", skill_name)
            return False

        # Restore from backup or from version file
        version_file = skill_dir / f"SKILL_v{from_version}.md"

        if version_file.exists():
            # Save current as backup before rolling back
            skill_file.rename(backup_file)
            version_file.rename(skill_file)
        else:
            logger.warning(
                "No version file for %s v%s — cannot rollback",
                skill_name, from_version,
            )
            return False

        # Update evolution_log
        import time
        await self.store.update_status(
            log_id,
            "rolled_back",
            rolledback_at=int(time.time()),
            rollback_reason=reason,
        )

        # Bump shared skills generation so user workspaces re-sync
        from main_server import _bump_shared_skills_gen
        _bump_shared_skills_gen()

        logger.info("Rolled back %s from v%s to v%s (reason: %s)", skill_name, to_version, from_version, reason)
        return True

    async def process_expired_reviews(self) -> int:
        """Auto-rollback all under_review evolutions past their 48h deadline.

        Returns count of rollbacks executed.
        """
        expired = await self.store.get_expired_reviews()
        count = 0
        for log in expired:
            success = await self.execute_rollback(log["id"], reason="48h auto-rollback")
            if success:
                count += 1
        return count
```

- [ ] **Step 2: Add rollback tests**

```python
# Append to tests/unit/test_evolution_evaluator.py

@pytest.mark.asyncio
async def test_rollback_restores_previous_version(db, tmp_path):
    import time
    from src.evolution_log import EvolutionLogStore
    from src.evolution_rollback import EvolutionRollback

    # Setup: create shared-skills dir with versioned files
    skills_dir = tmp_path / "shared-skills" / "test-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL_v1.0.md").write_text("# Old version")
    (skills_dir / "SKILL.md").write_text("# New broken version")

    store = EvolutionLogStore(db)
    r = await store.create_log("test-skill", "1.0", "1.1")
    log_id = r["id"]

    rollback = EvolutionRollback(db, tmp_path)
    import main_server
    main_server.DATA_ROOT = tmp_path

    result = await rollback.execute_rollback(log_id)
    assert result is True

    # Verify SKILL.md was restored to old version
    content = (skills_dir / "SKILL.md").read_text()
    assert content == "# Old version"

    # Verify backup was created
    assert (skills_dir / "SKILL_backup_v1.1.md").exists()

    # Verify log updated
    log = await store.get_log(log_id)
    assert log["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_process_expired_reviews_executes_rollbacks(db, tmp_path):
    import time
    from src.evolution_log import EvolutionLogStore
    from src.evolution_rollback import EvolutionRollback

    skills_dir = tmp_path / "shared-skills" / "skill-z"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL_v1.0.md").write_text("# Old")
    (skills_dir / "SKILL.md").write_text("# New")

    store = EvolutionLogStore(db)
    r = await store.create_log("skill-z", "1.0", "1.1")
    await store.update_status(r["id"], "under_review", auto_rollback_at=int(time.time()) - 3600)

    import main_server
    main_server.DATA_ROOT = tmp_path
    rollback = EvolutionRollback(db, tmp_path)

    count = await rollback.process_expired_reviews()
    assert count == 1

    log = await store.get_log(r["id"])
    assert log["status"] == "rolled_back"
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/unit/test_evolution_evaluator.py -v
```

Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add src/evolution_rollback.py tests/unit/test_evolution_evaluator.py
git commit -m "feat: add evolution rollback state machine"
```

---

### Task 5: Backend — session_learner.py (core ECC-inspired engine)

**Files:**
- Create: `src/session_learner.py`
- Create: `tests/unit/test_session_learner.py`

- [ ] **Step 1: Write the session learner**

```python
"""Session-based skill evolution — ECC-inspired continuous learning.

Triggered at session end. Queries the messages table for full conversation
context, calls Haiku for analysis, and applies improvements / creates new
skills based on confidence scores.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

if __name__ != "__main__":
    from src.database import Database
    from src.evolution_log import EvolutionLogStore

logger = logging.getLogger(__name__)

MIN_SESSION_MESSAGES = 10

ANALYSIS_PROMPT = """Analyze this AI agent session and identify what we can learn.

## Session Messages
{messages}

## Skills Used
{skills_used}

## Existing Feedback for These Skills
{existing_feedback}

## Tasks
1. For each skill used: did it perform well? If not, what went wrong and how should SKILL.md change?
2. Did the user demonstrate any reusable workflow that could become a new skill?

Return ONLY valid JSON (no markdown fences, no explanation):
{{
  "improvements": [
    {{"skill_name": "string", "confidence": 1-10, "issue": "specific description with context", "suggested_fix": "complete fixed SKILL.md content"}}
  ],
  "new_patterns": [
    {{"name": "kebab-case-name", "confidence": 1-10, "description": "what this pattern does and when to use it", "skill_content": "complete SKILL.md content"}}
  ]
}}"""


class SessionLearner:
    """Analyzes completed sessions and evolves skills.

    Uses callback injection to avoid circular imports with main_server:
    - skill_manager: SkillManager instance for DB registration
    - on_skill_changed: callable to bump shared-skills generation counter
      (triggers _sync_shared_skills for all users on their next session)
    """

    def __init__(
        self,
        db: "Database",
        data_root: Path,
        skill_manager: Any = None,
        on_skill_changed: "Callable[[], None] | None" = None,
    ) -> None:
        self.db = db
        self.data_root = data_root
        self.skill_manager = skill_manager
        self.on_skill_changed = on_skill_changed
        self.store = EvolutionLogStore(db)

    async def analyze_session(self, session_id: str) -> dict:
        """Analyze a completed session. Called as fire-and-forget at session end."""
        # 1. Check minimum message count
        msg_count = await self._count_messages(session_id)
        if msg_count < MIN_SESSION_MESSAGES:
            logger.debug("Session %s too short (%d messages), skipping", session_id, msg_count)
            return {"skipped": True, "reason": "too_short"}

        # 2. Query data
        messages = await self._get_session_messages(session_id)
        skills_used = await self._get_session_skills(session_id)
        if not skills_used:
            return {"skipped": True, "reason": "no_skills"}

        feedback = await self._get_skills_feedback(skills_used)

        # 3. Build prompt and call Haiku
        prompt = self._build_prompt(messages, skills_used, feedback)
        result = await self._call_haiku(prompt)
        if result is None:
            return {"skipped": True, "reason": "haiku_error"}

        # 4. Process results
        applied = []
        proposed = []
        for imp in result.get("improvements", []):
            conf = imp.get("confidence", 0)
            if conf >= 7:
                await self._apply_improvement(imp, session_id)
                applied.append(imp["skill_name"])
            elif conf >= 4:
                await self._propose_improvement(imp, session_id)
                proposed.append(imp["skill_name"])

        new_skills = []
        for pat in result.get("new_patterns", []):
            conf = pat.get("confidence", 0)
            if conf >= 7:
                await self._create_learned_skill(pat, session_id)
                new_skills.append(pat["name"])

        logger.info(
            "Session %s analysis: %d applied, %d proposed, %d new skills",
            session_id, len(applied), len(proposed), len(new_skills),
        )
        return {"applied": applied, "proposed": proposed, "new_skills": new_skills}

    # ── Data fetching ────────────────────────────────────────────

    async def _count_messages(self, session_id: str) -> int:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def _get_session_messages(self, session_id: str) -> list[dict]:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT seq, type, name, content
                   FROM messages WHERE session_id = ?
                   ORDER BY seq""",
                (session_id,),
            )
            return [dict(r) for r in await cursor.fetchall()]

    async def _get_session_skills(self, session_id: str) -> list[str]:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT DISTINCT skill_name FROM skill_usage WHERE session_id = ?",
                (session_id,),
            )
            return [r[0] for r in await cursor.fetchall()]

    async def _get_skills_feedback(self, skill_names: list[str]) -> dict:
        if not skill_names:
            return {}
        placeholders = ",".join("?" for _ in skill_names)
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                f"""SELECT skill_name, rating, comment
                    FROM skill_feedback
                    WHERE skill_name IN ({placeholders})
                    ORDER BY created_at DESC LIMIT 20""",
                skill_names,
            )
            rows = await cursor.fetchall()
            result: dict[str, list] = {}
            for r in rows:
                result.setdefault(r[0], []).append({"rating": r[1], "comment": r[2]})
            return result

    # ── Prompt & Haiku ────────────────────────────────────────────

    def _build_prompt(
        self,
        messages: list[dict],
        skills_used: list[str],
        feedback: dict,
    ) -> str:
        # Format messages compactly: skip empty content, truncate long lines
        lines = []
        for m in messages[-200:]:  # last 200 messages max to fit context
            content = (m.get("content") or "")[:300]
            if not content.strip():
                continue
            name = m.get("name") or ""
            prefix = f"[{m['seq']}] {m['type']}"
            if name:
                prefix += f":{name}"
            lines.append(f"{prefix} {content}")
        msg_text = "\n".join(lines)

        skills_text = ", ".join(skills_used)

        fb_lines = []
        for skill, entries in feedback.items():
            for e in entries[:3]:
                fb_lines.append(f"  {skill}: rating={e['rating']} — {e['comment'][:200]}")
        fb_text = "\n".join(fb_lines) if fb_lines else "No existing feedback"

        return ANALYSIS_PROMPT.format(
            messages=msg_text,
            skills_used=skills_text,
            existing_feedback=fb_text,
        )

    async def _call_haiku(self, prompt: str) -> dict | None:
        api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        if not api_key:
            logger.warning("SessionLearner: no API key available")
            return None

        try:
            import httpx

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 4000,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                    timeout=60.0,
                )
                resp.raise_for_status()
                data = resp.json()
                text = data["content"][0]["text"]

                # Strip markdown code fences if present
                if text.startswith("```"):
                    lines = text.split("\n")
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    text = "\n".join(lines)

                return json.loads(text)
        except Exception as e:
            logger.error("SessionLearner Haiku call failed: %s", e)
            return None

    # ── Apply / Propose / Create ──────────────────────────────────

    async def _apply_improvement(self, imp: dict, session_id: str) -> None:
        """Auto-apply a high-confidence skill improvement."""
        skill_name = imp["skill_name"]
        suggested_fix = imp["suggested_fix"]
        skill_dir = self.data_root / "shared-skills" / skill_name
        skill_file = skill_dir / "SKILL.md"

        if not skill_file.exists():
            return

        # Read current version
        old_content = skill_file.read_text()
        from_version = self._extract_version(old_content)

        # Create versioned backup
        existing_versions = list(skill_dir.glob("SKILL_v*.md"))
        next_version_num = len(existing_versions) + 1
        to_version = str(next_version_num)

        # Save old version
        backup_path = skill_dir / f"SKILL_v{from_version}.md"
        if not backup_path.exists():
            skill_file.rename(backup_path)
        else:
            skill_file.rename(skill_dir / f"SKILL_v{from_version}_backup_{next_version_num}.md")

        # Write new version
        skill_file.write_text(suggested_fix)

        # Create evolution_log entry
        await self.store.create_log(
            skill_name=skill_name,
            from_version=from_version,
            to_version=to_version,
            source="session_learner",
            evolve_reason=imp.get("issue", "")[:500],
        )

        # Bump shared skills generation so Windows copy-based sync refreshes
        # (Unix symlinks are transparent, but copies need re-sync)
        if self.on_skill_changed:
            self.on_skill_changed()

        logger.info("Applied improvement to %s: v%s → v%s", skill_name, from_version, to_version)

    async def _propose_improvement(self, imp: dict, session_id: str) -> None:
        """Save a medium-confidence improvement as a proposal in skill_feedback."""
        async with self.db.connection() as conn:
            await conn.execute(
                """INSERT INTO skill_feedback
                   (skill_name, user_id, session_id, rating, comment, user_edits)
                   VALUES (?, ?, ?, 3, ?, ?)""",
                (
                    imp["skill_name"],
                    "system",
                    session_id,
                    f"[AI PROPOSAL] {imp.get('issue', '')[:400]}",
                    imp.get("suggested_fix", ""),
                ),
            )

    async def _create_learned_skill(self, pat: dict, session_id: str) -> None:
        """Create a new learned skill from a discovered pattern.

        Makes the skill discoverable via three channels simultaneously:
        1. Disk — SKILL.md + skill-meta.json in shared-skills/{name}/
        2. DB — SkillManager.register_skill(source='learned')
        3. Sync — _bump_shared_skills_gen() triggers workspace sync for all users
        """
        import json
        name = pat["name"].strip().lower().replace(" ", "-")
        description = pat.get("description", "")
        skill_dir = self.data_root / "shared-skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)

        content = pat.get("skill_content", "")
        if not content:
            content = f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{description}"

        (skill_dir / "SKILL.md").write_text(content)

        # Write skill-meta.json so migrate_from_filesystem() can read metadata
        meta = {"owner": "system", "description": description, "source": "learned"}
        (skill_dir / "skill-meta.json").write_text(json.dumps(meta, indent=2))

        # Register in DB via SkillManager (idempotent — ON CONFLICT safe)
        if self.skill_manager is not None:
            await self.skill_manager.register_skill(
                skill_name=name,
                source="learned",
                owner_id="system",
                description=description,
                path=str(skill_dir),
            )

        # Create evolution_log entry
        await self.store.create_log(
            skill_name=name,
            from_version="0",
            to_version="1.0",
            source="session_learner",
            evolve_reason=f"New pattern: {description[:500]}",
        )

        # Bump generation so all users sync this skill on next session
        # (Unix: creates symlink; Windows: copies directory)
        if self.on_skill_changed:
            self.on_skill_changed()

        logger.info("Created learned skill: %s (disk + DB + gen-bump)", name)

    @staticmethod
    def _extract_version(content: str) -> str:
        """Extract version from SKILL.md frontmatter."""
        for line in content.split("\n"):
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip()
        return "1.0"
```

- [ ] **Step 2: Write tests**

```python
# tests/unit/test_session_learner.py
"""Tests for session_learner."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_analyze_skips_short_sessions(db, tmp_path):
    from src.session_learner import SessionLearner
    learner = SessionLearner(db, tmp_path)
    result = await learner.analyze_session("nonexistent")
    assert result["skipped"] is True
    assert result["reason"] == "too_short"


@pytest.mark.asyncio
async def test_build_prompt_formats_messages(db, tmp_path):
    from src.session_learner import SessionLearner
    learner = SessionLearner(db, tmp_path)
    messages = [
        {"seq": 1, "type": "user", "name": None, "content": "Hello"},
        {"seq": 2, "type": "assistant", "name": None, "content": "Hi there"},
    ]
    prompt = learner._build_prompt(messages, ["code-reviewer"], {})
    assert "[1] user Hello" in prompt
    assert "code-reviewer" in prompt


@pytest.mark.asyncio
async def test_parse_haiku_response_applies_high_confidence(db, tmp_path):
    from src.session_learner import SessionLearner
    from src.evolution_log import EvolutionLogStore

    store = EvolutionLogStore(db)
    learner = SessionLearner(db, tmp_path)

    # Setup: create a skill file to improve
    skill_dir = tmp_path / "shared-skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: test-skill\nversion: 1.0\n---\n\n# Old")

    imp = {
        "skill_name": "test-skill",
        "confidence": 8,
        "issue": "bad error handling",
        "suggested_fix": "---\nname: test-skill\nversion: 1.0\n---\n\n# Fixed",
    }

    import main_server
    main_server.DATA_ROOT = tmp_path

    await learner._apply_improvement(imp, "sess_1")

    # Verify new content was written
    content = (skill_dir / "SKILL.md").read_text()
    assert "# Fixed" in content

    # Verify evolution_log entry
    logs = await store.list_logs(skill_name="test-skill")
    assert logs["total"] == 1
    assert logs["items"][0]["source"] == "session_learner"


@pytest.mark.asyncio
async def test_create_learned_skill(db, tmp_path):
    from src.session_learner import SessionLearner
    learner = SessionLearner(db, tmp_path)

    pat = {
        "name": "debug-pattern",
        "confidence": 8,
        "description": "A reusable debugging technique",
        "skill_content": "---\nname: debug-pattern\ndescription: Debug helper\n---\n\n# Debug",
    }

    import main_server
    main_server.DATA_ROOT = tmp_path

    await learner._create_learned_skill(pat, "sess_1")

    skill_file = tmp_path / "shared-skills" / "debug-pattern" / "SKILL.md"
    assert skill_file.exists()
    content = skill_file.read_text()
    assert "Debug helper" in content


@pytest.mark.asyncio
async def test_extract_version_from_frontmatter():
    from src.session_learner import SessionLearner
    content = "---\nname: foo\nversion: 2.3\n---\n\n# Body"
    assert SessionLearner._extract_version(content) == "2.3"


@pytest.mark.asyncio
async def test_extract_version_default():
    from src.session_learner import SessionLearner
    assert SessionLearner._extract_version("# No frontmatter") == "1.0"
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/unit/test_session_learner.py -v
```

Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add src/session_learner.py tests/unit/test_session_learner.py
git commit -m "feat: add ECC-inspired session learner for skill evolution"
```

---

### Task 6: Backend — collective_intelligence.py changes

**Files:**
- Modify: `src/collective_intelligence.py`

- [ ] **Step 1: Add _eval_snapshot_loop and simplify auto_evolve**

Read the current `collective_intelligence.py`. Make these changes:

```python
# Add to imports
from src.evolution_evaluator import EvolutionEvaluator
from src.evolution_rollback import EvolutionRollback

# In start_background_jobs(), replace _auto_evolve_loop with _eval_snapshot_loop:
async def start_background_jobs(self) -> None:
    asyncio.create_task(self._wiki_mining_loop())
    asyncio.create_task(self._pattern_extraction_loop())
    asyncio.create_task(self._auto_promotion_loop())
    asyncio.create_task(self._eval_snapshot_loop())   # <-- replaces _auto_evolve_loop
    logger.info("Collective intelligence background jobs started")

# New method (add to CollectiveIntelligenceEngine):
async def _eval_snapshot_loop(self) -> None:
    """Daily evaluation: snapshot active evolutions, detect degradation, auto-rollback."""
    evaluator = EvolutionEvaluator(self.db)
    rollback = EvolutionRollback(self.db, self.data_root)

    while True:
        try:
            # Wait until 02:00 local time, then run once per day
            now = datetime.now()
            next_run = now.replace(hour=2, minute=0, second=0, microsecond=0)
            if now >= next_run:
                next_run += timedelta(days=1)
            wait_seconds = (next_run - now).total_seconds()
            await asyncio.sleep(wait_seconds)

            await evaluator.run_daily_eval()
            count = await rollback.process_expired_reviews()
            if count:
                logger.info("Auto-rolled back %d degraded evolutions", count)
        except Exception:
            logger.exception("Eval snapshot loop failed")
            await asyncio.sleep(3600)  # retry after 1h on error

# Keep _auto_evolve_loop but make it a no-op (keep import compatibility):
async def _auto_evolve_loop(self) -> None:
    """Deprecated: evolution is now driven by session_learner at session end."""
    logger.debug("_auto_evolve_loop is deprecated — evolution now via session_learner")
    # Keep the loop alive but sleeping to avoid breaking callers
    while True:
        await asyncio.sleep(86400)
```

- [ ] **Step 2: Commit**

```bash
git add src/collective_intelligence.py
git commit -m "feat: replace auto_evolve loop with daily eval snapshot loop"
```

---

### Task 7: Backend — main_server.py (session-end trigger + admin APIs)

**Files:**
- Modify: `main_server.py`
- Create: `tests/unit/test_evolution_api.py`

- [ ] **Step 1: Add session-end trigger (both modes)**

In `run_agent_task()` (non-container) AND `run_agent_task_container()` (container), after the `"completed"` branch's `agent_log.end_session()` and `_summarize_and_store_session()`, add:

```python
# Fire-and-forget session analysis (don't block agent task cleanup)
asyncio.ensure_future(_analyze_completed_session(session_id))
```

And add the helper function (module level):

```python
async def _analyze_completed_session(session_id: str) -> None:
    """Fire-and-forget session analysis for skill evolution.

    Runs on the host side for BOTH container and non-container modes.
    Injects main_server globals via constructor to avoid circular imports.
    """
    try:
        from src.session_learner import SessionLearner
        learner = SessionLearner(
            _db,
            DATA_ROOT,
            skill_manager=_skill_manager,
            on_skill_changed=_bump_shared_skills_gen,
        )
        await learner.analyze_session(session_id)
    except Exception:
        logger.exception("Session analysis failed for %s", session_id)
```

- [ ] **Step 2: Add 4 admin API endpoints**

```python
# ── Evolution Admin APIs ──────────────────────────────────────────

@app.get("/api/admin/evolution/overview")
async def evolution_overview(
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    admin: dict = Depends(require_admin),
):
    """List all evolution records with optional status filter."""
    from src.evolution_log import EvolutionLogStore
    store = EvolutionLogStore(_db)
    return await store.list_logs(status=status, page=page, page_size=page_size)


@app.get("/api/admin/evolution/{evolution_id}")
async def evolution_detail(
    evolution_id: int,
    admin: dict = Depends(require_admin),
):
    """Get evolution detail with snapshots and signal breakdown."""
    from src.evolution_log import EvolutionLogStore
    store = EvolutionLogStore(_db)
    log = await store.get_log(evolution_id)
    if not log:
        raise HTTPException(404, "Evolution record not found")

    snaps = await store.get_snapshots(evolution_id)

    # Build signal breakdown
    if snaps:
        current = snaps[-1]
        signal_breakdown = {
            "rating": {"current": current["avg_rating"], "baseline": 3.0, "delta_pct": 0},
            "usage": {"current": current["usage_count"], "baseline": 5, "delta_pct": 0},
            "session_success": {"current": current["session_success_rate"], "baseline": 0.8, "delta_pct": 0},
        }
    else:
        signal_breakdown = {}

    return {
        **log,
        "snapshots": snaps,
        "signal_breakdown": signal_breakdown,
    }


@app.get("/api/admin/evolution/{evolution_id}/diff")
async def evolution_diff(
    evolution_id: int,
    admin: dict = Depends(require_admin),
):
    """Get SKILL.md diff between from_version and to_version."""
    from src.evolution_log import EvolutionLogStore
    store = EvolutionLogStore(_db)
    log = await store.get_log(evolution_id)
    if not log:
        raise HTTPException(404, "Evolution record not found")

    skill_name = log["skill_name"]
    from_ver = log["from_version"]
    to_ver = log["to_version"]

    skill_dir = DATA_ROOT / "shared-skills" / skill_name

    # Read old version
    old_file = skill_dir / f"SKILL_v{from_ver}.md"
    old_content = old_file.read_text() if old_file.exists() else ""

    # Read current version
    new_file = skill_dir / "SKILL.md"
    new_content = new_file.read_text() if new_file.exists() else ""

    import difflib
    diff_lines = list(
        difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"{skill_name}/v{from_ver}",
            tofile=f"{skill_name}/v{to_ver}",
        )
    )

    return {
        "from_version": from_ver,
        "to_version": to_ver,
        "diff": "".join(diff_lines),
    }


@app.post("/api/admin/evolution/{evolution_id}/review")
async def evolution_review(
    evolution_id: int,
    decision: dict,
    admin: dict = Depends(require_admin),
):
    """Admin reviews an under_review evolution: keep or rollback."""
    d = decision.get("decision")
    if d not in ("keep", "rollback"):
        raise HTTPException(422, "decision must be 'keep' or 'rollback'")

    from src.evolution_log import EvolutionLogStore
    from src.evolution_rollback import EvolutionRollback
    import time

    store = EvolutionLogStore(_db)
    log = await store.get_log(evolution_id)
    if not log:
        raise HTTPException(404, "Evolution record not found")

    if d == "keep":
        await store.update_status(
            evolution_id,
            "active",
            reviewed_at=int(time.time()),
            reviewed_by=admin["user_id"],
            review_decision="kept",
        )
        return {"status": "active", "message": "Evolution kept"}
    else:
        rollback = EvolutionRollback(_db, DATA_ROOT)
        success = await rollback.execute_rollback(
            evolution_id, reason=f"Admin rollback by {admin['user_id']}"
        )
        if not success:
            raise HTTPException(500, "Rollback failed — version file not found")
        # Update review metadata
        await store.update_status(
            evolution_id,
            "rolled_back",
            reviewed_at=int(time.time()),
            reviewed_by=admin["user_id"],
            review_decision="rolled_back",
        )
        return {"status": "rolled_back", "message": "Evolution rolled back"}
```

- [ ] **Step 3: Write API tests**

```python
# tests/unit/test_evolution_api.py
"""Tests for evolution admin APIs."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_mock_sdk = MagicMock()
_mock_sdk.types = MagicMock()
_mock_sdk.types.UserMessage = MagicMock
sys.modules["claude_agent_sdk"] = _mock_sdk
sys.modules["claude_agent_sdk.types"] = _mock_sdk.types

from fastapi.testclient import TestClient

import main_server
import src.auth
import src.admin_auth
src.auth.ENFORCE_AUTH = False
src.admin_auth.ENFORCE_AUTH = False


@pytest.fixture(autouse=True)
def _patch_data_root(tmp_path: Path) -> None:
    main_server.DATA_ROOT = tmp_path
    main_server.buffer = main_server.MessageBuffer()
    main_server.active_tasks.clear()
    main_server.pending_answers.clear()
    (tmp_path / "users").mkdir(exist_ok=True)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(main_server.app)


class TestEvolutionOverview:
    def test_returns_empty_list_with_no_evolutions(self, client):
        resp = client.get("/api/admin/evolution/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_rejects_non_admin(self):
        """Skipped in test mode — ENFORCE_AUTH is False."""
        pass


class TestEvolutionDetail:
    def test_404_for_missing_evolution(self, client):
        resp = client.get("/api/admin/evolution/99999")
        assert resp.status_code == 404


class TestEvolutionReview:
    def test_422_for_invalid_decision(self, client):
        resp = client.post("/api/admin/evolution/1/review", json={"decision": "maybe"})
        assert resp.status_code == 422

    def test_valid_keep_decision(self, client):
        """Needs a real evolution_log entry — integration test."""
        pass
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_evolution_api.py -v
```

Expected: tests pass

- [ ] **Step 5: Commit**

```bash
git add main_server.py tests/unit/test_evolution_api.py
git commit -m "feat: add session-end trigger and admin evolution APIs"
```

---

### Task 8: Frontend — useEvolutionApi hook

**Files:**
- Create: `frontend/src/hooks/useEvolutionApi.ts`

- [ ] **Step 1: Write the data hook**

```typescript
import { useState, useEffect, useCallback, useMemo } from 'react'

export interface EvolutionItem {
  id: number
  skill_name: string
  from_version: string
  to_version: string
  source: string
  evolve_reason: string
  status: 'active' | 'under_review' | 'rolled_back' | 'superseded'
  created_at: number
  reviewed_at: number | null
  reviewed_by: string | null
  review_decision: string | null
  auto_rollback_at: number | null
  days_active?: number
  composite_score?: number
}

export interface EvolutionDetail extends EvolutionItem {
  snapshots: Snapshot[]
  signal_breakdown: SignalBreakdown | null
}

export interface Snapshot {
  snapshot_date: string
  usage_count: number
  unique_users: number
  avg_rating: number
  session_success_rate: number
  composite_score: number
}

export interface SignalBreakdown {
  rating: { current: number; baseline: number; delta_pct: number }
  usage: { current: number; baseline: number; delta_pct: number }
  session_success: { current: number; baseline: number; delta_pct: number }
}

export interface EvolutionDiff {
  from_version: string
  to_version: string
  diff: string
}

interface AsyncState<T> {
  data: T | null
  loading: boolean
  error: string | null
}

export interface EvolutionApi {
  overview: AsyncState<{ items: EvolutionItem[]; total: number; page: number }>
  detail: (id: number) => AsyncState<EvolutionDetail>
  diff: (id: number) => AsyncState<EvolutionDiff>
  review: (id: number, decision: 'keep' | 'rollback') => Promise<void>
  refetch: () => void
}

const API_BASE = '/api/admin/evolution'

async function fetchJson<T>(url: string, token: string): Promise<T> {
  const headers: Record<string, string> = token
    ? { Authorization: `Bearer ${token}` }
    : {}
  const resp = await fetch(url, { headers })
  if (!resp.ok) {
    const detail = await resp.json().then((b) => b.detail).catch(() => resp.statusText)
    throw new Error(typeof detail === 'string' ? detail : resp.statusText)
  }
  return resp.json() as Promise<T>
}

export function useEvolutionApi(statusFilter?: string, page: number = 1): EvolutionApi {
  const authToken = useMemo(() => localStorage.getItem('authToken') || '', [])
  const [refreshKey, setRefreshKey] = useState(0)

  const [overview, setOverview] = useState<AsyncState<{ items: EvolutionItem[]; total: number; page: number }>>({
    data: null, loading: true, error: null,
  })

  const fetchOverview = useCallback(() => {
    setOverview((s) => ({ ...s, loading: true, error: null }))
    const params = new URLSearchParams()
    if (statusFilter) params.set('status', statusFilter)
    params.set('page', String(page))
    fetchJson<{ items: EvolutionItem[]; total: number; page: number }>(
      `${API_BASE}/overview?${params}`, authToken,
    )
      .then((data) => setOverview({ data, loading: false, error: null }))
      .catch((e: unknown) =>
        setOverview({ data: null, loading: false, error: e instanceof Error ? e.message : 'Unknown error' }),
      )
  }, [authToken, statusFilter, page, refreshKey])

  useEffect(() => {
    fetchOverview()
  }, [fetchOverview])

  const getDetail = useCallback((id: number): AsyncState<EvolutionDetail> => {
    const [state, setState] = useState<AsyncState<EvolutionDetail>>({ data: null, loading: true, error: null })
    useEffect(() => {
      fetchJson<EvolutionDetail>(`${API_BASE}/${id}`, authToken)
        .then((data) => setState({ data, loading: false, error: null }))
        .catch((e: unknown) =>
          setState({ data: null, loading: false, error: e instanceof Error ? e.message : 'Unknown error' }),
        )
    }, [id, authToken])
    return state
  }, [authToken])

  const getDiff = useCallback((id: number): AsyncState<EvolutionDiff> => {
    const [state, setState] = useState<AsyncState<EvolutionDiff>>({ data: null, loading: true, error: null })
    useEffect(() => {
      fetchJson<EvolutionDiff>(`${API_BASE}/${id}/diff`, authToken)
        .then((data) => setState({ data, loading: false, error: null }))
        .catch((e: unknown) =>
          setState({ data: null, loading: false, error: e instanceof Error ? e.message : 'Unknown error' }),
        )
    }, [id, authToken])
    return state
  }, [authToken])

  const review = useCallback(async (id: number, decision: 'keep' | 'rollback') => {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    }
    if (authToken) headers['Authorization'] = `Bearer ${authToken}`
    const resp = await fetch(`${API_BASE}/${id}/review`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ decision }),
    })
    if (!resp.ok) {
      const detail = await resp.json().then((b) => b.detail).catch(() => resp.statusText)
      throw new Error(typeof detail === 'string' ? detail : resp.statusText)
    }
    setRefreshKey((k) => k + 1)
  }, [authToken])

  const refetch = useCallback(() => {
    setRefreshKey((k) => k + 1)
  }, [])

  return { overview, detail: getDetail, diff: getDiff, review, refetch }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/hooks/useEvolutionApi.ts
git commit -m "feat: add useEvolutionApi hook for evolution dashboard"
```

---

### Task 9: Frontend — EvolutionPage components

**Files:**
- Create: `frontend/src/pages/EvolutionPage.tsx`
- Create: `frontend/src/pages/evolution/OverviewTable.tsx`
- Create: `frontend/src/pages/evolution/ScoreTrendChart.tsx`
- Create: `frontend/src/pages/evolution/SignalBreakdown.tsx`
- Create: `frontend/src/pages/evolution/VersionDiff.tsx`
- Create: `frontend/src/pages/evolution/RollbackTimeline.tsx`
- Create: `frontend/src/pages/evolution/evolution.css`
- Modify: `frontend/src/App.tsx` (add route)
- Modify: `frontend/package.json` (add `@monaco-editor/react`)

- [ ] **Step 1: Install Monaco dependency**

```bash
cd frontend && npm install @monaco-editor/react
```

- [ ] **Step 2: Write EvolutionPage.tsx**

```tsx
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useEvolutionApi, type EvolutionItem } from '../hooks/useEvolutionApi'
import OverviewTable from './evolution/OverviewTable'
import EvolutionDetail from './evolution/EvolutionDetail'
import './evolution/evolution.css'

type View = 'overview' | { detail: number }

export default function EvolutionPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [statusFilter, setStatusFilter] = useState<string | undefined>()
  const [view, setView] = useState<View>('overview')
  const api = useEvolutionApi(statusFilter)

  const handleRowClick = (item: EvolutionItem) => {
    setView({ detail: item.id })
  }

  const handleBack = () => {
    setView('overview')
  }

  if (typeof view === 'object' && 'detail' in view) {
    return (
      <div className="evolution-page">
        <button className="evolution-back" onClick={handleBack}>
          ← {t('common.back')}
        </button>
        <EvolutionDetail evolutionId={view.detail} />
      </div>
    )
  }

  return (
    <div className="evolution-page">
      <button className="evolution-back" onClick={() => navigate('/')}>
        {t('common.back')}
      </button>

      <div className="evolution-header">
        <h2>CI Evolution Monitor</h2>
        <div className="status-tabs">
          {['All', 'Active', 'Under Review', 'Rolled Back'].map((label) => {
            const value = label === 'All' ? undefined : label.toLowerCase().replace(' ', '_')
            return (
              <button
                key={label}
                className={`tab-btn ${statusFilter === value ? 'active' : ''}`}
                onClick={() => setStatusFilter(value)}
              >
                {label}
              </button>
            )
          })}
        </div>
      </div>

      <OverviewTable
        data={api.overview.data}
        loading={api.overview.loading}
        error={api.overview.error}
        onRowClick={handleRowClick}
      />
    </div>
  )
}
```

- [ ] **Step 3: Write OverviewTable.tsx**

```tsx
import type { EvolutionItem } from '../../hooks/useEvolutionApi'

interface Props {
  data: { items: EvolutionItem[]; total: number; page: number } | null
  loading: boolean
  error: string | null
  onRowClick: (item: EvolutionItem) => void
}

const STATUS_LABELS: Record<string, { label: string; className: string }> = {
  active: { label: 'Active', className: 'status-active' },
  under_review: { label: 'Under Review', className: 'status-review' },
  rolled_back: { label: 'Rolled Back', className: 'status-rolled' },
}

export default function OverviewTable({ data, loading, error, onRowClick }: Props) {
  if (loading) return <div className="evo-loading">Loading...</div>
  if (error) return <div className="evo-error">{error}</div>
  if (!data || data.items.length === 0) {
    return <div className="evo-empty">No evolution records found.</div>
  }

  return (
    <table className="evo-table">
      <thead>
        <tr>
          <th>Skill</th>
          <th>Version</th>
          <th>Source</th>
          <th>Status</th>
          <th>Created</th>
        </tr>
      </thead>
      <tbody>
        {data.items.map((item) => {
          const s = STATUS_LABELS[item.status] || { label: item.status, className: '' }
          return (
            <tr key={item.id} onClick={() => onRowClick(item)} className="evo-row">
              <td>{item.skill_name}</td>
              <td>v{item.from_version} → v{item.to_version}</td>
              <td>{item.source}</td>
              <td><span className={`evo-badge ${s.className}`}>{s.label}</span></td>
              <td>{new Date(item.created_at * 1000).toLocaleDateString()}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
```

- [ ] **Step 4: Write EvolutionDetail.tsx**

```tsx
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { EvolutionDetail as EvoDetail } from '../../hooks/useEvolutionApi'
import ScoreTrendChart from './ScoreTrendChart'
import SignalBreakdown from './SignalBreakdown'
import VersionDiff from './VersionDiff'
import RollbackTimeline from './RollbackTimeline'

const API_BASE = '/api/admin/evolution'

async function fetchDetail(id: number, token: string): Promise<EvoDetail> {
  const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {}
  const resp = await fetch(`${API_BASE}/${id}`, { headers })
  if (!resp.ok) throw new Error('Failed to load')
  return resp.json()
}

export default function EvolutionDetail({ evolutionId }: { evolutionId: number }) {
  const { t } = useTranslation()
  const [data, setData] = useState<EvoDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState(false)

  useEffect(() => {
    const token = localStorage.getItem('authToken') || ''
    fetchDetail(evolutionId, token)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [evolutionId])

  const handleReview = async (decision: 'keep' | 'rollback') => {
    setActionLoading(true)
    try {
      const token = localStorage.getItem('authToken') || ''
      const resp = await fetch(`${API_BASE}/${evolutionId}/review`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ decision }),
      })
      if (!resp.ok) throw new Error('Failed')
      // Refresh
      const updated = await fetchDetail(evolutionId, token)
      setData(updated)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setActionLoading(false)
    }
  }

  if (loading) return <div className="evo-loading">Loading...</div>
  if (error) return <div className="evo-error">{error}</div>
  if (!data) return <div className="evo-empty">Not found</div>

  return (
    <div className="evo-detail">
      <h3>{data.skill_name}: v{data.from_version} → v{data.to_version}</h3>
      <p className="evo-meta">
        Source: {data.source} | Status: {data.status} | Created: {new Date(data.created_at * 1000).toLocaleString()}
      </p>
      {data.evolve_reason && <p className="evo-reason">{data.evolve_reason}</p>}

      {(data.snapshots?.length ?? 0) > 0 && (
        <>
          <ScoreTrendChart snapshots={data.snapshots!} />
          {data.signal_breakdown && <SignalBreakdown breakdown={data.signal_breakdown} />}
        </>
      )}

      <VersionDiff evolutionId={evolutionId} />
      {data.status === 'under_review' && (
        <div className="evo-actions">
          <button
            className="btn-keep"
            disabled={actionLoading}
            onClick={() => handleReview('keep')}
          >
            Keep
          </button>
          <button
            className="btn-rollback"
            disabled={actionLoading}
            onClick={() => handleReview('rollback')}
          >
            Rollback
          </button>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 5: Write ScoreTrendChart.tsx**

```tsx
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'
import type { Snapshot } from '../../hooks/useEvolutionApi'

interface Props {
  snapshots: Snapshot[]
}

export default function ScoreTrendChart({ snapshots }: Props) {
  const data = snapshots.map((s) => ({
    date: s.snapshot_date,
    score: s.composite_score,
    baseline: 0.6,
  }))

  return (
    <div className="evo-chart">
      <h4>Composite Score Trend</h4>
      <ResponsiveContainer width="100%" height={250}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="date" />
          <YAxis domain={[0, 1]} />
          <Tooltip />
          <Line type="monotone" dataKey="score" stroke="#3b82f6" name="Current" />
          <Line type="monotone" dataKey="baseline" stroke="#9ca3af" strokeDasharray="5 5" name="Baseline" />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
```

- [ ] **Step 6: Write SignalBreakdown.tsx**

```tsx
import type { SignalBreakdown as SB } from '../../hooks/useEvolutionApi'

interface Props {
  breakdown: SB
}

export default function SignalBreakdown({ breakdown }: Props) {
  return (
    <div className="signal-breakdown">
      <div className="signal-card">
        <h5>User Rating</h5>
        <div className="signal-value">{breakdown.rating.current.toFixed(1)} / 5</div>
        <div className="signal-delta negative">↓ {Math.abs(breakdown.rating.delta_pct)}%</div>
      </div>
      <div className="signal-card">
        <h5>Usage</h5>
        <div className="signal-value">{breakdown.usage.current} / day</div>
        <div className="signal-delta negative">↓ {Math.abs(breakdown.usage.delta_pct)}%</div>
      </div>
      <div className="signal-card">
        <h5>Session Success</h5>
        <div className="signal-value">{(breakdown.session_success.current * 100).toFixed(0)}%</div>
        <div className="signal-delta negative">↓ {Math.abs(breakdown.session_success.delta_pct)}%</div>
      </div>
    </div>
  )
}
```

- [ ] **Step 7: Write VersionDiff.tsx**

```tsx
import { useState, useEffect } from 'react'
import { DiffEditor } from '@monaco-editor/react'
import type { EvolutionDiff } from '../../hooks/useEvolutionApi'

interface Props {
  evolutionId: number
}

export default function VersionDiff({ evolutionId }: Props) {
  const [data, setData] = useState<EvolutionDiff | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const token = localStorage.getItem('authToken') || ''
    const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {}
    fetch(`/api/admin/evolution/${evolutionId}/diff`, { headers })
      .then((r) => r.json())
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [evolutionId])

  if (loading) return <div className="evo-loading">Loading diff...</div>
  if (!data) return <div className="evo-empty">Diff not available</div>

  // Split diff into original and modified for Monaco
  const lines = data.diff.split('\n')
  const originalLines: string[] = []
  const modifiedLines: string[] = []

  for (const line of lines) {
    if (line.startsWith('-') && !line.startsWith('---')) {
      originalLines.push(line.slice(1))
    } else if (line.startsWith('+') && !line.startsWith('+++')) {
      modifiedLines.push(line.slice(1))
    } else if (line.startsWith(' ') || line === '') {
      originalLines.push(line.startsWith(' ') ? line.slice(1) : line)
      modifiedLines.push(line.startsWith(' ') ? line.slice(1) : line)
    }
  }

  return (
    <div className="evo-diff">
      <h4>Version Diff</h4>
      <DiffEditor
        original={originalLines.join('\n')}
        modified={modifiedLines.join('\n')}
        language="markdown"
        options={{ readOnly: true, renderSideBySide: true }}
        height="400px"
      />
    </div>
  )
}
```

- [ ] **Step 8: Write RollbackTimeline.tsx**

```tsx
interface TimelineEvent {
  date: string
  event: string
}

export default function RollbackTimeline() {
  return (
    <div className="evo-timeline">
      <h4>Rollback Timeline</h4>
      <div className="timeline-placeholder">
        Timeline events will appear here when evolution state changes occur.
      </div>
    </div>
  )
}
```

- [ ] **Step 9: Write evolution.css**

```css
.evolution-page {
  max-width: 1200px;
  margin: 0 auto;
  padding: 2rem;
}

.evolution-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1.5rem;
}

.status-tabs {
  display: flex;
  gap: 0.5rem;
}

.tab-btn {
  padding: 0.5rem 1rem;
  border: 1px solid var(--border-color, #ddd);
  border-radius: 6px;
  background: transparent;
  cursor: pointer;
  font-size: 0.875rem;
}

.tab-btn.active {
  background: var(--accent, #3b82f6);
  color: white;
  border-color: var(--accent, #3b82f6);
}

.evo-table {
  width: 100%;
  border-collapse: collapse;
}

.evo-table th {
  text-align: left;
  padding: 0.75rem;
  border-bottom: 2px solid var(--border-color, #eee);
  font-weight: 600;
  font-size: 0.875rem;
}

.evo-table td {
  padding: 0.75rem;
  border-bottom: 1px solid var(--border-color, #eee);
  font-size: 0.875rem;
}

.evo-row {
  cursor: pointer;
}

.evo-row:hover {
  background: var(--hover-bg, #f9fafb);
}

.evo-badge {
  display: inline-block;
  padding: 0.25rem 0.625rem;
  border-radius: 999px;
  font-size: 0.75rem;
  font-weight: 500;
}

.status-active { background: #dcfce7; color: #166534; }
.status-review { background: #fef9c3; color: #854d0e; }
.status-rolled { background: #fee2e2; color: #991b1b; }

.evo-detail { margin-top: 1rem; }

.evo-meta { color: #6b7280; margin-bottom: 0.5rem; }

.evo-reason {
  background: #f9fafb;
  padding: 0.75rem;
  border-radius: 6px;
  margin-bottom: 1.5rem;
}

.evo-chart {
  margin-bottom: 2rem;
}

.evo-chart h4 {
  margin-bottom: 0.5rem;
}

.signal-breakdown {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
  margin-bottom: 2rem;
}

.signal-card {
  padding: 1rem;
  border: 1px solid var(--border-color, #eee);
  border-radius: 8px;
  text-align: center;
}

.signal-card h5 {
  margin: 0 0 0.25rem 0;
  font-size: 0.75rem;
  text-transform: uppercase;
  color: #6b7280;
}

.signal-value {
  font-size: 1.5rem;
  font-weight: 700;
  margin-bottom: 0.25rem;
}

.signal-delta {
  font-size: 0.875rem;
  color: #ef4444;
}

.evo-diff {
  margin-bottom: 2rem;
}

.evo-diff h4 {
  margin-bottom: 0.5rem;
}

.evo-actions {
  display: flex;
  gap: 1rem;
  margin-top: 1.5rem;
}

.btn-keep {
  padding: 0.625rem 1.5rem;
  background: #059669;
  color: white;
  border: none;
  border-radius: 6px;
  cursor: pointer;
}

.btn-keep:hover { background: #047857; }

.btn-rollback {
  padding: 0.625rem 1.5rem;
  background: #dc2626;
  color: white;
  border: none;
  border-radius: 6px;
  cursor: pointer;
}

.btn-rollback:hover { background: #b91c1c; }

.evolution-back {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--accent, #3b82f6);
  margin-bottom: 1rem;
  font-size: 0.875rem;
}
```

- [ ] **Step 10: Add route in App.tsx**

In `App.tsx`, add the import and route:

```tsx
// Add with other lazy imports
const EvolutionPage = React.lazy(() => import('./pages/EvolutionPage'))

// Add route inside the admin-guarded Routes
<Route
  path="/dashboard/evolution"
  element={
    <Suspense fallback={<div>Loading...</div>}>
      <EvolutionPage />
    </Suspense>
  }
/>
```

- [ ] **Step 11: Commit**

```bash
git add frontend/
git commit -m "feat: add CI evolution admin dashboard page"
```

---

### Task 10: Integration — wire everything together and verify

**Files:**
- No new files; verify all components work together

- [ ] **Step 1: Run all backend tests**

```bash
uv run pytest tests/unit/test_session_learner.py tests/unit/test_evolution_evaluator.py tests/unit/test_evolution_api.py -v
```

Expected: all tests pass

- [ ] **Step 2: Run frontend type check**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no type errors

- [ ] **Step 3: Start dev server and verify UI**

```bash
# Terminal 1: Backend
uv run uvicorn main_server:app --reload --port 8000

# Terminal 2: Frontend
cd frontend && npm run dev
```

Navigate to `/dashboard/evolution`, verify:
- Page loads without errors
- Empty state shows "No evolution records found"
- Admin APIs return 200 (test with curl)

- [ ] **Step 4: Verify OS & mode compatibility**

```bash
# Verify no circular imports (session_learner must not import from main_server)
uv run python -c "
from src.session_learner import SessionLearner
print('OK: session_learner imports without main_server')
"

# Verify SessionLearner constructor accepts callback injection
uv run python -c "
from pathlib import Path
from src.session_learner import SessionLearner
learner = SessionLearner(None, Path('/tmp'), skill_manager=None, on_skill_changed=lambda: None)
print('OK: callback injection works')
"

# Verify gen bump is callable
uv run python -c "
# Simulate what main_server does
called = []
def bump():
    called.append(1)
from src.session_learner import SessionLearner
learner = SessionLearner(None, Path('/tmp'), skill_manager=None, on_skill_changed=bump)
assert learner.on_skill_changed is not None
learner.on_skill_changed()
assert called == [1]
print('OK: on_skill_changed callback functional')
"
```

- [ ] **Step 5: Commit final integration fixes**

```bash
git add -A
git commit -m "chore: final integration fixes for CI evolution dashboard"
```

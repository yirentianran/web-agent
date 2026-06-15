# Evolution Dashboard Improvement Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix six dashboard issues: real signal baselines, consistent funnel time window, observation detail display, time range selector, richer evolution table, and auto-refresh.

**Architecture:** Backend changes add a `baseline_metrics` JSON column to `evolution_log`, a `days` parameter to stats, and missing fields to observation API. Frontend adds a time range selector bar, new table columns, auto-refresh polling, and richer signal breakdown display.

**Tech Stack:** Python/FastAPI backend, React/TypeScript frontend, SQLite, recharts

---

### Task 1: Database — Add baseline_metrics column

**Files:**
- Modify: `src/database.py:850-880` (add migrate_v9)

- [ ] **Step 1: Add migration function to Database class**

In `src/database.py`, after `migrate_v8`, add:

```python
async def migrate_v9(self) -> None:
    """Add baseline_metrics JSON column to evolution_log."""
    async with self.connection() as conn:
        await conn.execute(
            "ALTER TABLE evolution_log ADD COLUMN baseline_metrics TEXT"
        )
```

- [ ] **Step 2: Register migration in run_migrations**

In `src/database.py`, find the block of `try: await self.migrate_v8()` around line 405 and add after it:

```python
# Add baseline_metrics to evolution_log
try:
    await self.migrate_v9()
except Exception:
    pass
```

- [ ] **Step 3: Verify migration**

```bash
cd /Users/mac/Documents/Projects/web-agent && uv run python -c "
from src.database import Database
from pathlib import Path
import asyncio
async def test():
    db = Database(Path('data/web-agent.db'))
    await db.init()
    async with db.connection() as conn:
        cols = await conn.execute_fetchall(\"PRAGMA table_info(evolution_log)\")
        names = [c[1] for c in cols]
        print('baseline_metrics exists:', 'baseline_metrics' in names)
asyncio.run(test())
"
```

Expected: `baseline_metrics exists: True`

- [ ] **Step 4: Commit**

```bash
git add src/database.py
git commit -m "feat: add baseline_metrics column to evolution_log"
```

---

### Task 2: Backend — Compute and store pre-evolution baseline

**Files:**
- Modify: `src/evolution_log.py:17-39` (create_log signature)
- Modify: `src/instinct_extractor.py:486-540` (_apply_skill_change, _propose_skill_change)

- [ ] **Step 1: Add baseline_metrics param to create_log**

In `src/evolution_log.py`, update `create_log`:

```python
async def create_log(
    self,
    skill_name: str,
    from_version: str,
    to_version: str,
    *,
    source: str = "session_learner",
    evolve_reason: str = "",
    proposed_content: str = "",
    baseline_composite: float | None = None,
    baseline_metrics: str = "",
    status: str = "active",
) -> dict[str, Any]:
    async with self.db.connection() as conn:
        cursor = await conn.execute(
            """INSERT INTO evolution_log
               (skill_name, from_version, to_version, source, evolve_reason,
                proposed_content, baseline_composite, baseline_metrics, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (skill_name, from_version, to_version, source, evolve_reason,
             proposed_content, baseline_composite, baseline_metrics, status,
             int(time.time())),
        )
        return {"id": cursor.lastrowid}
```

- [ ] **Step 2: Add _compute_baseline_metrics helper to InstinctExtractor**

In `src/instinct_extractor.py`, add a method to the `InstinctExtractor` class (after `_read_current_skill` around line 484):

```python
import json as _json

async def _compute_baseline_metrics(self) -> dict[str, float]:
    """Compute pre-evolution metrics from the last 7 days of observations."""
    cutoff = time.time() - 7 * 86400
    async with self.db.connection() as conn:
        rows = await conn.execute_fetchall(
            """SELECT success, COUNT(*) as cnt FROM observations
               WHERE created_at >= ? AND event_type = 'tool_call_end'
               AND success IS NOT NULL
               GROUP BY success""",
            (cutoff,),
        )
    total = sum(r[1] for r in rows)
    success_count = sum(r[1] for r in rows if r[0] == 1)
    tool_success_rate = success_count / total if total > 0 else 1.0

    async with self.db.connection() as conn:
        session_rows = await conn.execute_fetchall(
            """SELECT event_type, COUNT(DISTINCT session_id) as cnt
               FROM observations WHERE created_at >= ?
               AND event_type IN ('session_complete', 'session_error')
               GROUP BY event_type""",
            (cutoff,),
        )
    sc = {r[0]: r[1] for r in session_rows}
    completed = sc.get("session_complete", 0)
    errored = sc.get("session_error", 0)
    session_rate = completed / (completed + errored) if (completed + errored) > 0 else 1.0

    composite = 0.5 * tool_success_rate + 0.3 * session_rate + 0.2 * min(1.0, total / 50)

    return {
        "tool_success_rate": round(tool_success_rate, 4),
        "session_success_rate": round(session_rate, 4),
        "daily_usage": total,
        "composite_score": round(composite, 4),
    }
```

- [ ] **Step 3: Update _apply_skill_change to capture and pass baseline**

In `src/instinct_extractor.py`, update `_apply_skill_change` to add baseline computation before the `create_log` call:

```python
async def _apply_skill_change(
    self,
    skill_name: str,
    new_content: str,
    instinct_ids: list[int],
    cluster: list[dict[str, Any]],
) -> None:
    from pathlib import Path
    import shutil
    import json as _json

    skill_dir = Path(self.data_root) / "shared-skills" / skill_name
    skill_file = skill_dir / "SKILL.md"

    versions_dir = skill_dir / "versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    existing = [d.name for d in versions_dir.iterdir() if d.name.startswith("v")]
    next_v = len(existing) + 1
    v_dir = versions_dir / f"v{next_v}"
    v_dir.mkdir()
    if skill_file.exists():
        shutil.copy2(skill_file, v_dir / "SKILL.md")

    skill_file.write_text(new_content)

    baseline = await self._compute_baseline_metrics()

    log = await self.evolution_store.create_log(
        skill_name=skill_name,
        from_version=f"v{next_v}",
        to_version=f"v{next_v + 1}",
        source="instinct_extractor",
        evolve_reason=f"Auto-applied cluster: {cluster[0]['normalized_trigger']}",
        proposed_content="",
        baseline_composite=baseline["composite_score"],
        baseline_metrics=_json.dumps(baseline),
        status="active",
    )

    await self.instinct_store.link_to_evolution(instinct_ids, log["id"])
```

- [ ] **Step 4: Update _propose_skill_change similarly**

In `src/instinct_extractor.py`, update `_propose_skill_change`:

```python
async def _propose_skill_change(
    self,
    skill_name: str,
    new_content: str,
    instinct_ids: list[int],
    cluster: list[dict[str, Any]],
) -> None:
    import json as _json
    baseline = await self._compute_baseline_metrics()
    log = await self.evolution_store.create_log(
        skill_name=skill_name,
        from_version="current",
        to_version="proposed",
        source="instinct_extractor",
        evolve_reason=f"Proposed cluster: {cluster[0]['normalized_trigger']}",
        proposed_content=new_content,
        baseline_composite=baseline["composite_score"],
        baseline_metrics=_json.dumps(baseline),
        status="proposed",
    )
    await self.instinct_store.link_to_evolution(instinct_ids, log["id"])
```

- [ ] **Step 5: Run existing tests to verify no breakage**

```bash
cd /Users/mac/Documents/Projects/web-agent && uv run pytest tests/unit/test_instinct_extractor.py tests/unit/test_evolution_log.py -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/evolution_log.py src/instinct_extractor.py
git commit -m "feat: capture pre-evolution baseline metrics when creating evolution"
```

---

### Task 3: Backend — observation list returns tool_input_summary and tool_output_summary

**Files:**
- Modify: `src/observation.py:82-129` (list_events)

- [ ] **Step 1: Add fields to list_events query and response**

In `src/observation.py`, update the `list_events` method:

```python
async def list_events(
    self,
    *,
    session_id: str = "",
    event_type: str = "",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    conditions = []
    params: list[Any] = []
    if session_id:
        conditions.append("session_id = ?")
        params.append(session_id)
    if event_type:
        conditions.append("event_type = ?")
        params.append(event_type)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    async with self.db.connection() as conn:
        count_row = await conn.execute_fetchall(
            f"SELECT COUNT(*) FROM observations {where}", params
        )
        total = count_row[0][0] if count_row else 0

        offset = (page - 1) * page_size
        cursor = await conn.execute(
            f"""SELECT id, session_id, user_id, event_type, tool_name,
                       tool_input_summary, tool_output_summary,
                       success, error_message, duration_ms, created_at
                FROM observations {where}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?""",
            params + [page_size, offset],
        )
        rows = await cursor.fetchall()

    return {
        "items": [
            {
                "id": r[0], "session_id": r[1], "user_id": r[2],
                "event_type": r[3], "tool_name": r[4],
                "tool_input_summary": r[5] or "",
                "tool_output_summary": r[6] or "",
                "success": bool(r[7]) if r[7] is not None else None,
                "error_message": r[8], "duration_ms": r[9], "created_at": r[10],
            }
            for r in rows
        ],
        "total": total,
        "page": page,
    }
```

- [ ] **Step 2: Run observation tests**

```bash
cd /Users/mac/Documents/Projects/web-agent && uv run pytest tests/unit/test_observation.py -v
```

Expected: Tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/observation.py
git commit -m "feat: return tool_input_summary and tool_output_summary in observation list"
```

---

### Task 4: Backend — API endpoints: days param, real baseline, observation fields

**Files:**
- Modify: `main_server.py:5948-6224` (evolution routes)
- Modify: `src/evolution_log.py:201-239` (get_overview_stats)

- [ ] **Step 1: Add days param to get_overview_stats**

In `src/evolution_log.py`, update `get_overview_stats`:

```python
async def get_overview_stats(self, days: int = 0) -> dict[str, Any]:
    """Dashboard stats. days=0 means today only, days>0 means last N days."""
    if days > 0:
        cutoff = time.time() - days * 86400
    else:
        cutoff = time.time() - (time.time() % 86400)

    async with self.db.connection() as conn:
        status_rows = await conn.execute_fetchall(
            "SELECT status, COUNT(*) FROM evolution_log GROUP BY status"
        )
        status_counts = {r[0]: r[1] for r in status_rows}

        instinct_active_row = await conn.execute_fetchall(
            "SELECT COUNT(*) FROM instincts WHERE scope = 'active'"
        )
        instinct_active = instinct_active_row[0][0] if instinct_active_row else 0

        obs_count_row = await conn.execute_fetchall(
            "SELECT COUNT(*) FROM observations WHERE created_at >= ?",
            (cutoff,),
        )
        events_in_window = obs_count_row[0][0] if obs_count_row else 0

        evo_active_row = await conn.execute_fetchall(
            """SELECT COUNT(*) FROM evolution_log
               WHERE status = 'active' AND created_at >= ?""",
            (cutoff,),
        )
        evo_active_in_window = evo_active_row[0][0] if evo_active_row else 0

        evo_proposed_row = await conn.execute_fetchall(
            """SELECT COUNT(*) FROM evolution_log
               WHERE status = 'proposed' AND created_at >= ?""",
            (cutoff,),
        )
        evo_proposed_in_window = evo_proposed_row[0][0] if evo_proposed_row else 0

        week_applied_row = await conn.execute_fetchall(
            """SELECT COUNT(*) FROM evolution_log
               WHERE status = 'active' AND source = 'instinct_extractor'
               AND created_at >= ?""",
            (time.time() - 7 * 86400,),
        )
        week_auto = week_applied_row[0][0] if week_applied_row else 0

    if days == 0:
        today_events = events_in_window
    else:
        today_events_row = await conn.execute_fetchall(
            "SELECT COUNT(*) FROM observations WHERE created_at >= ?",
            (time.time() - (time.time() % 86400),),
        )
        today_events = today_events_row[0][0] if today_events_row else 0

    return {
        "today_events": today_events,
        "active_instincts": instinct_active,
        "pending_reviews": status_counts.get("proposed", 0),
        "week_auto_applied": week_auto,
        "funnel": {
            "observations": events_in_window,
            "active_instincts": instinct_active,
            "active_evolutions": evo_active_in_window,
            "proposed_evolutions": evo_proposed_in_window,
        },
        "time_window": f"last_{days}_days" if days > 0 else "today",
    }
```

Wait — the above has an issue: `conn` is used after the first `async with` block exits. Let me restructure:

```python
async def get_overview_stats(self, days: int = 0) -> dict[str, Any]:
    """Dashboard stats. days=0 means today only, days>0 means last N days."""
    if days > 0:
        cutoff = time.time() - days * 86400
    else:
        cutoff = time.time() - (time.time() % 86400)
    week_cutoff = time.time() - 7 * 86400

    async with self.db.connection() as conn:
        status_rows = await conn.execute_fetchall(
            "SELECT status, COUNT(*) FROM evolution_log GROUP BY status"
        )
        status_counts = {r[0]: r[1] for r in status_rows}

        instinct_active_row = await conn.execute_fetchall(
            "SELECT COUNT(*) FROM instincts WHERE scope = 'active'"
        )
        instinct_active = instinct_active_row[0][0] if instinct_active_row else 0

        obs_count = await conn.execute_fetchall(
            "SELECT COUNT(*) FROM observations WHERE created_at >= ?", (cutoff,),
        )
        events_in_window = obs_count[0][0] if obs_count else 0

        evo_active = await conn.execute_fetchall(
            "SELECT COUNT(*) FROM evolution_log WHERE status = 'active' AND created_at >= ?",
            (cutoff,),
        )
        evo_active_in_window = evo_active[0][0] if evo_active else 0

        evo_proposed = await conn.execute_fetchall(
            "SELECT COUNT(*) FROM evolution_log WHERE status = 'proposed' AND created_at >= ?",
            (cutoff,),
        )
        evo_proposed_in_window = evo_proposed[0][0] if evo_proposed else 0

        week_applied = await conn.execute_fetchall(
            """SELECT COUNT(*) FROM evolution_log
               WHERE status = 'active' AND source = 'instinct_extractor'
               AND created_at >= ?""",
            (week_cutoff,),
        )
        week_auto = week_applied[0][0] if week_applied else 0

        if days == 0:
            today_events = events_in_window
        else:
            today_start = time.time() - (time.time() % 86400)
            today_row = await conn.execute_fetchall(
                "SELECT COUNT(*) FROM observations WHERE created_at >= ?",
                (today_start,),
            )
            today_events = today_row[0][0] if today_row else 0

    return {
        "today_events": today_events,
        "active_instincts": instinct_active,
        "pending_reviews": status_counts.get("proposed", 0),
        "week_auto_applied": week_auto,
        "funnel": {
            "observations": events_in_window,
            "active_instincts": instinct_active,
            "active_evolutions": evo_active_in_window,
            "proposed_evolutions": evo_proposed_in_window,
        },
        "time_window": f"last_{days}_days" if days > 0 else "today",
    }
```

- [ ] **Step 2: Update stats endpoint with days param**

In `main_server.py`, update the stats endpoint:

```python
@app.get("/api/admin/evolution/stats")
async def evolution_stats(
    days: int = 0,
    current_user: str = Depends(require_admin),
):
    """Dashboard stats for the instinct evolution panel."""
    from src.evolution_log import EvolutionLogStore

    store = EvolutionLogStore(_db)
    return await store.get_overview_stats(days=days)
```

- [ ] **Step 3: Update detail endpoint to use stored baseline_metrics**

In `main_server.py`, replace the detail endpoint's signal breakdown logic (lines 5993-6037):

```python
@app.get("/api/admin/evolution/{evolution_id}")
async def evolution_detail(
    evolution_id: int,
    current_user: str = Depends(require_admin),
):
    """Get evolution detail with snapshots and signal breakdown."""
    import json as _json
    from src.evolution_log import EvolutionLogStore

    store = EvolutionLogStore(_db)
    log = await store.get_log(evolution_id)
    if not log:
        raise HTTPException(404, "Evolution record not found")

    log_with_instincts = await store.get_log_with_instincts(evolution_id)
    snaps = await store.get_snapshots(evolution_id)

    # Parse stored baseline metrics, fall back to hardcoded defaults
    stored_baseline: dict = {}
    if log.get("baseline_metrics"):
        try:
            stored_baseline = _json.loads(log["baseline_metrics"])
        except (_json.JSONDecodeError, TypeError):
            pass

    baseline_rating = stored_baseline.get("avg_rating", 4.0)
    baseline_usage = stored_baseline.get("daily_usage", 10)
    baseline_success = stored_baseline.get("session_success_rate", 0.80)

    if snaps and log.get("baseline_composite"):
        current_snap = snaps[-1]
        signal_breakdown = {
            "rating": {
                "current": current_snap.get("avg_rating", 0),
                "baseline": baseline_rating,
                "delta_pct": round(
                    (current_snap.get("avg_rating", 0) - baseline_rating) / baseline_rating * 100, 1
                ) if baseline_rating and current_snap.get("avg_rating") else 0,
            },
            "usage": {
                "current": current_snap.get("usage_count", 0),
                "baseline": baseline_usage,
                "delta_pct": round(
                    (current_snap.get("usage_count", 0) - baseline_usage) / baseline_usage * 100, 1
                ) if baseline_usage and current_snap.get("usage_count") else 0,
            },
            "session_success": {
                "current": current_snap.get("session_success_rate", 0),
                "baseline": baseline_success,
                "delta_pct": round(
                    (current_snap.get("session_success_rate", 0) - baseline_success) / baseline_success * 100, 1
                ) if baseline_success and current_snap.get("session_success_rate") else 0,
            },
        }
    else:
        signal_breakdown = None

    return {
        **log_with_instincts,
        "snapshots": snaps,
        "signal_breakdown": signal_breakdown,
    }
```

- [ ] **Step 4: Add days_active and composite_score to overview items**

In `main_server.py`, update `evolution_overview` (line 5948-5971) to add `days_active` and `composite_score`:

```python
@app.get("/api/admin/evolution/overview")
async def evolution_overview(
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    current_user: str = Depends(require_admin),
):
    """List all evolution records with optional status filter."""
    from src.evolution_log import EvolutionLogStore
    import time as _time

    store = EvolutionLogStore(_db)
    result = await store.list_logs(status=status, page=page, page_size=page_size)

    now = _time.time()
    for item in result["items"]:
        async with _db.connection() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM instincts WHERE source_evolution_id = ?",
                (item["id"],),
            )
            row = await cursor.fetchone()
            item["instinct_count"] = row[0] if row else 0

            # Latest snapshot composite score
            snap_cursor = await conn.execute(
                """SELECT composite_score FROM skill_eval_snapshots
                   WHERE evolution_log_id = ? ORDER BY snapshot_date DESC LIMIT 1""",
                (item["id"],),
            )
            snap_row = await snap_cursor.fetchone()
            item["composite_score"] = round(snap_row[0], 4) if snap_row and snap_row[0] else None

        days_active = (now - item["created_at"]) / 86400
        item["days_active"] = max(1, int(days_active))

    return result
```

- [ ] **Step 5: Run backend tests**

```bash
cd /Users/mac/Documents/Projects/web-agent && uv run pytest tests/unit/test_evolution_api.py -v
```

Expected: Tests pass.

- [ ] **Step 6: Commit**

```bash
git add main_server.py src/evolution_log.py
git commit -m "feat: days param for stats, real baseline in detail, richer overview"
```

---

### Task 5: Backend — EvolutionSignals uses stored baseline

**Files:**
- Modify: `src/evolution_signals.py:118-119` (_compute_baseline)

- [ ] **Step 1: Use stored baseline_metrics in _compute_baseline**

In `src/evolution_signals.py`, update `_compute_baseline`:

```python
import json as _json

def _compute_baseline(self, log: dict[str, Any]) -> float:
    if log.get("baseline_metrics"):
        try:
            bl = _json.loads(log["baseline_metrics"])
            return bl.get("composite_score", 0.6)
        except (_json.JSONDecodeError, TypeError):
            pass
    return log.get("baseline_composite") or 0.6
```

- [ ] **Step 2: Run signals tests**

```bash
cd /Users/mac/Documents/Projects/web-agent && uv run pytest tests/unit/test_evolution_signals.py -v
```

- [ ] **Step 3: Commit**

```bash
git add src/evolution_signals.py
git commit -m "feat: EvolutionSignals reads baseline from stored metrics"
```

---

### Task 6: Frontend — useEvolutionApi hook: new types and params

**Files:**
- Modify: `frontend/src/hooks/useEvolutionApi.ts`

- [ ] **Step 1: Add fields to ObservationItem type**

In `frontend/src/hooks/useEvolutionApi.ts`, update `ObservationItem` (line 62-72):

```typescript
export interface ObservationItem {
  id: number
  session_id: string
  user_id: string
  event_type: string
  tool_name: string
  tool_input_summary: string
  tool_output_summary: string
  success: boolean | null
  error_message: string
  duration_ms: number
  created_at: number
}
```

- [ ] **Step 2: Add days_active and composite_score to EvolutionItem**

In `frontend/src/hooks/useEvolutionApi.ts`, update `EvolutionItem` (line 3-20):

```typescript
export interface EvolutionItem {
  id: number
  skill_name: string
  from_version: string
  to_version: string
  source: string
  evolve_reason: string
  status: 'active' | 'under_review' | 'rolled_back' | 'proposed' | 'superseded'
  baseline_composite: number | null
  baseline_metrics: string | null
  proposed_content: string | null
  instinct_count: number
  composite_score: number | null
  days_active: number
  created_at: number
  reviewed_at: number | null
  reviewed_by: string | null
  review_decision: string | null
  auto_rollback_at: number | null
}
```

- [ ] **Step 3: Add time_window to EvolutionStats**

In `frontend/src/hooks/useEvolutionApi.ts`, update `EvolutionStats` (line 74-85):

```typescript
export interface EvolutionStats {
  today_events: number
  active_instincts: number
  pending_reviews: number
  week_auto_applied: number
  time_window: string
  funnel: {
    observations: number
    active_instincts: number
    active_evolutions: number
    proposed_evolutions: number
  }
}
```

- [ ] **Step 4: Add days param to fetchStats**

In `frontend/src/hooks/useEvolutionApi.ts`, update `fetchStats` (line 198):

```typescript
const fetchStats = useCallback(async (days: number = 0) => {
  setStats((s) => ({ ...s, loading: true, error: null }))
  try {
    const qs = days > 0 ? `?days=${days}` : ''
    const data = await fetchJson<EvolutionStats>(
      `${API_BASE}/stats${qs}`,
      authToken,
    )
    setStats({ data, loading: false, error: null })
  } catch (e: unknown) {
    setStats({
      data: null,
      loading: false,
      error: e instanceof Error ? e.message : 'Unknown error',
    })
  }
}, [authToken])
```

- [ ] **Step 5: Update EvolutionApi interface to match**

In `frontend/src/hooks/useEvolutionApi.ts`, update the interface (line 117):

```typescript
fetchStats: (days?: number) => Promise<void>
```

- [ ] **Step 6: Frontend type check**

```bash
cd /Users/mac/Documents/Projects/web-agent/frontend && npx tsc --noEmit
```

Fix any type errors.

- [ ] **Step 7: Commit**

```bash
cd /Users/mac/Documents/Projects/web-agent && git add frontend/src/hooks/useEvolutionApi.ts
git commit -m "feat: update EvolutionApi types with new fields and days param"
```

---

### Task 7: Frontend — Time range selector and auto-refresh in EvolutionPage

**Files:**
- Modify: `frontend/src/pages/EvolutionPage.tsx`

- [ ] **Step 1: Add time range and auto-refresh state to EvolutionPage**

Replace `EvolutionPage.tsx`:

```tsx
import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { StatsCards } from './evolution/StatsCards';
import { PipelineFunnel } from './evolution/PipelineFunnel';
import OverviewTable from './evolution/OverviewTable';
import EvolutionDetail from './evolution/EvolutionDetail';
import { InstinctList } from './evolution/InstinctList';
import { ObservationBrowser } from './evolution/ObservationBrowser';
import { useEvolutionApi } from '../hooks/useEvolutionApi';
import './evolution/evolution.css';

type TabId = 'evolutions' | 'instincts' | 'observations';

const TIME_RANGES: { days: number; labelKey: string }[] = [
  { days: 0, labelKey: 'evolutionMonitor.timeToday' },
  { days: 7, labelKey: 'evolutionMonitor.time7Days' },
  { days: 30, labelKey: 'evolutionMonitor.time30Days' },
  { days: 90, labelKey: 'evolutionMonitor.timeAll' },
];

export default function EvolutionPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const api = useEvolutionApi();
  const [activeTab, setActiveTab] = useState<TabId>('evolutions');
  const [detailId, setDetailId] = useState<number | null>(null);
  const [timeRange, setTimeRange] = useState(0);
  const refreshRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadData = useCallback((days: number) => {
    api.fetchStats(days);
    api.fetchInstincts({});
    api.fetchObservations({});
  }, [api]);

  useEffect(() => {
    loadData(timeRange);
  }, [timeRange]);

  // Auto-refresh every 30s when not viewing detail
  useEffect(() => {
    if (detailId !== null) {
      if (refreshRef.current) {
        clearInterval(refreshRef.current);
        refreshRef.current = null;
      }
      return;
    }
    refreshRef.current = setInterval(() => loadData(timeRange), 30000);
    return () => {
      if (refreshRef.current) {
        clearInterval(refreshRef.current);
        refreshRef.current = null;
      }
    };
  }, [detailId, timeRange, loadData]);

  const handleInstinctFilter = useCallback(
    (filters: { domain?: string; scope?: string }) => {
      api.fetchInstincts(filters);
    },
    [api.fetchInstincts]
  );

  const handleObsFilter = useCallback(
    (filters: { session_id?: string; event_type?: string }) => {
      api.fetchObservations(filters);
    },
    [api.fetchObservations]
  );

  const TABS: { id: TabId; labelKey: string }[] = [
    { id: 'evolutions', labelKey: 'evolutionMonitor.evolutionsTab' },
    { id: 'instincts', labelKey: 'evolutionMonitor.instinctsTab' },
    { id: 'observations', labelKey: 'evolutionMonitor.observationsTab' },
  ];

  if (detailId !== null) {
    return (
      <div className="evolution-page detail-page">
        <div className="evolution-header skills-header detail-header">
          <button
            className="evolution-back-btn skills-back-btn detail-back-btn"
            onClick={() => setDetailId(null)}
            type="button"
          >
            {t('evolutionMonitor.backToOverview')}
          </button>
          <div className="evolution-header-title-group skills-header-title-group">
            <h2>{t('evolutionMonitor.title')}</h2>
          </div>
        </div>
        <EvolutionDetail evolutionId={detailId} api={api} />
      </div>
    );
  }

  return (
    <div className="evolution-page detail-page">
      <div className="evolution-header skills-header detail-header">
        <button
          className="evolution-back-btn skills-back-btn detail-back-btn"
          onClick={() => navigate('/')}
          type="button"
        >
          {t('common.back')}
        </button>
        <div className="evolution-header-title-group skills-header-title-group">
          <h2>{t('evolutionMonitor.title')}</h2>
        </div>
      </div>

      {/* Time range selector */}
      <div className="time-range-bar">
        {TIME_RANGES.map(({ days, labelKey }) => (
          <button
            key={days}
            className={`time-range-btn ${timeRange === days ? 'active' : ''}`}
            onClick={() => setTimeRange(days)}
          >
            {t(labelKey)}
          </button>
        ))}
        <span className="auto-refresh-indicator" title={t('evolutionMonitor.autoRefresh')}>
          {t('evolutionMonitor.autoRefresh')}
        </span>
      </div>

      <StatsCards stats={api.stats.data ?? null} loading={api.stats.loading} />
      <PipelineFunnel stats={api.stats.data ?? null} />

      <div className="skills-tabs">
        {TABS.map(({ id, labelKey }) => (
          <button
            key={id}
            className={`skills-tab ${activeTab === id ? 'active' : ''}`}
            onClick={() => setActiveTab(id)}
          >
            {t(labelKey)}
          </button>
        ))}
      </div>

      {activeTab === 'evolutions' && (
        <OverviewTable
          data={api.overview.data}
          loading={api.overview.loading}
          error={api.overview.error}
          onRowClick={(item) => setDetailId(item.id)}
        />
      )}
      {activeTab === 'instincts' && (
        <InstinctList
          data={api.instincts.data}
          loading={api.instincts.loading}
          error={api.instincts.error}
          onFilterChange={handleInstinctFilter}
        />
      )}
      {activeTab === 'observations' && (
        <ObservationBrowser
          data={api.observations.data}
          loading={api.observations.loading}
          error={api.observations.error}
          onFilterChange={handleObsFilter}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 2: Frontend type check**

```bash
cd /Users/mac/Documents/Projects/web-agent/frontend && npx tsc --noEmit
```

Fix any type errors.

- [ ] **Step 3: Commit**

```bash
cd /Users/mac/Documents/Projects/web-agent && git add frontend/src/pages/EvolutionPage.tsx
git commit -m "feat: add time range selector and auto-refresh to evolution dashboard"
```

---

### Task 8: Frontend — PipelineFunnel shows time window label

**Files:**
- Modify: `frontend/src/pages/evolution/PipelineFunnel.tsx`

- [ ] **Step 1: Add time window label**

Replace `PipelineFunnel.tsx`:

```tsx
import React from 'react';
import { useTranslation } from 'react-i18next';
import type { EvolutionStats } from '../../hooks/useEvolutionApi';

interface Props {
  stats: EvolutionStats | null;
}

const WINDOW_LABELS: Record<string, string> = {
  today: 'evolutionMonitor.timeToday',
  last_7_days: 'evolutionMonitor.time7Days',
  last_30_days: 'evolutionMonitor.time30Days',
  last_90_days: 'evolutionMonitor.timeAll',
};

export const PipelineFunnel: React.FC<Props> = ({ stats }) => {
  const { t } = useTranslation();
  if (!stats) return null;
  const { funnel, time_window } = stats;
  const stages = [
    { labelKey: 'evolutionMonitor.observations', value: funnel.observations, key: 'observations' },
    { labelKey: 'evolutionMonitor.instincts', value: funnel.active_instincts, key: 'instincts' },
    { labelKey: 'evolutionMonitor.evolutionsStage', value: funnel.active_evolutions, key: 'evolutions' },
    { labelKey: 'evolutionMonitor.proposed', value: funnel.proposed_evolutions, key: 'proposed' },
  ];
  const maxVal = Math.max(...stages.map((s) => s.value), 1);

  return (
    <div className="pipeline-funnel">
      {stages.map(({ labelKey, value, key }, i) => (
        <React.Fragment key={key}>
          {i > 0 && <span className="funnel-arrow">→</span>}
          <div className="funnel-stage">
            <span className="funnel-label">{t(labelKey)}</span>
            <span className="funnel-value">{value}</span>
            <div className="funnel-bar">
              <div
                className="funnel-bar-fill"
                style={{ width: `${(value / maxVal) * 100}%` }}
              />
            </div>
          </div>
        </React.Fragment>
      ))}
      {time_window && (
        <p className="funnel-window-label">
          {t(WINDOW_LABELS[time_window] || time_window)}
        </p>
      )}
    </div>
  );
};
```

- [ ] **Step 2: Commit**

```bash
cd /Users/mac/Documents/Projects/web-agent && git add frontend/src/pages/evolution/PipelineFunnel.tsx
git commit -m "feat: show time window label on pipeline funnel"
```

---

### Task 9: Frontend — OverviewTable with new columns

**Files:**
- Modify: `frontend/src/pages/evolution/OverviewTable.tsx`

- [ ] **Step 1: Add instinct_count, composite_score, days_active columns**

Replace `OverviewTable.tsx`:

```tsx
import { useTranslation } from 'react-i18next';
import type { EvolutionItem } from '../../hooks/useEvolutionApi'

interface Props {
  data: { items: EvolutionItem[]; total: number; page: number } | null
  loading: boolean
  error: string | null
  onRowClick: (item: EvolutionItem) => void
}

const STATUS_CLASSES: Record<string, string> = {
  active: 'status-active',
  proposed: 'status-proposed',
  under_review: 'status-review',
  rolled_back: 'status-rolled',
  superseded: 'status-rolled',
}

function ScoreBadge({ score }: { score: number | null | undefined }) {
  if (score == null) return <span className="score-na">—</span>
  const color = score >= 0.7 ? 'score-good' : score >= 0.5 ? 'score-warn' : 'score-bad'
  return <span className={`score-badge ${color}`}>{(score * 100).toFixed(0)}%</span>
}

export default function OverviewTable({
  data,
  loading,
  error,
  onRowClick,
}: Props) {
  const { t } = useTranslation();

  if (loading) return <div className="evo-loading">{t('common.loading')}</div>
  if (error) return <div className="evo-error">{error}</div>
  if (!data || data.items.length === 0) {
    return <div className="evo-empty">{t('evolutionMonitor.noEvolutions')}</div>
  }

  return (
    <table className="evo-table">
      <thead>
        <tr>
          <th>{t('evolutionMonitor.skill')}</th>
          <th>{t('evolutionMonitor.version')}</th>
          <th>{t('evolutionMonitor.instinctCount')}</th>
          <th>{t('evolutionMonitor.compositeScore')}</th>
          <th>{t('evolutionMonitor.daysActive')}</th>
          <th>{t('evolutionMonitor.source')}</th>
          <th>{t('evolutionMonitor.status')}</th>
          <th>{t('evolutionMonitor.created')}</th>
        </tr>
      </thead>
      <tbody>
        {data.items.map((item) => (
          <tr
            key={item.id}
            onClick={() => onRowClick(item)}
            className="evo-row"
          >
            <td>{item.skill_name}</td>
            <td>
              v{item.from_version} → v{item.to_version}
            </td>
            <td>{item.instinct_count ?? 0}</td>
            <td><ScoreBadge score={item.composite_score} /></td>
            <td>{item.days_active ?? 1}d</td>
            <td>{item.source}</td>
            <td>
              <span className={`evo-badge ${STATUS_CLASSES[item.status] || ''}`}>
                {item.status}
              </span>
            </td>
            <td>{new Date(item.created_at * 1000).toLocaleDateString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd /Users/mac/Documents/Projects/web-agent && git add frontend/src/pages/evolution/OverviewTable.tsx
git commit -m "feat: add instinct count, composite score, days active to evolution table"
```

---

### Task 10: Frontend — SignalBreakdown shows baseline source

**Files:**
- Modify: `frontend/src/pages/evolution/SignalBreakdown.tsx`

- [ ] **Step 1: Add baseline label to each signal card**

Replace `SignalBreakdown.tsx`:

```tsx
import type { SignalBreakdown as SB } from '../../hooks/useEvolutionApi'

interface Props {
  breakdown: SB
}

function DeltaIndicator({ deltaPct }: { deltaPct: number }) {
  const isPositive = deltaPct >= 0
  return (
    <div className={`signal-delta ${isPositive ? 'positive' : 'negative'}`}>
      {isPositive ? '↑' : '↓'} {Math.abs(deltaPct).toFixed(1)}%
    </div>
  )
}

export default function SignalBreakdown({ breakdown }: Props) {
  return (
    <div className="signal-breakdown">
      <div className="signal-card">
        <h5>User Rating</h5>
        <div className="signal-value">
          {breakdown.rating.current.toFixed(1)} / 5
        </div>
        <DeltaIndicator deltaPct={breakdown.rating.delta_pct} />
        <div className="signal-baseline">
          Baseline: {breakdown.rating.baseline.toFixed(1)}
        </div>
      </div>
      <div className="signal-card">
        <h5>Usage</h5>
        <div className="signal-value">{breakdown.usage.current} / day</div>
        <DeltaIndicator deltaPct={breakdown.usage.delta_pct} />
        <div className="signal-baseline">
          Baseline: {breakdown.usage.baseline.toFixed(0)} / day
        </div>
      </div>
      <div className="signal-card">
        <h5>Session Success</h5>
        <div className="signal-value">
          {(breakdown.session_success.current * 100).toFixed(0)}%
        </div>
        <DeltaIndicator deltaPct={breakdown.session_success.delta_pct} />
        <div className="signal-baseline">
          Baseline: {(breakdown.session_success.baseline * 100).toFixed(0)}%
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd /Users/mac/Documents/Projects/web-agent && git add frontend/src/pages/evolution/SignalBreakdown.tsx
git commit -m "feat: show baseline values in signal breakdown cards"
```

---

### Task 11: Frontend — ObservationBrowser with input/output columns

**Files:**
- Modify: `frontend/src/pages/evolution/ObservationBrowser.tsx`

- [ ] **Step 1: Add input_summary and output_summary columns**

Replace the table in `ObservationBrowser.tsx` (lines 48-70):

```tsx
{!loading && !error && data && data.items.length > 0 && (
  <>
    <table className="evo-table">
      <thead>
        <tr>
          <th>{t('evolutionMonitor.id')}</th>
          <th>{t('evolutionMonitor.session')}</th>
          <th>{t('evolutionMonitor.type')}</th>
          <th>{t('evolutionMonitor.tool')}</th>
          <th>{t('evolutionMonitor.inputSummary')}</th>
          <th>{t('evolutionMonitor.outputSummary')}</th>
          <th>{t('evolutionMonitor.success')}</th>
          <th>{t('evolutionMonitor.time')}</th>
        </tr>
      </thead>
      <tbody>
        {data.items.map((obs) => (
          <tr key={obs.id} className="evo-row">
            <td>{obs.id}</td>
            <td>{obs.session_id.substring(0, 12)}...</td>
            <td><span className="evo-badge">{obs.event_type}</span></td>
            <td>{obs.tool_name || '—'}</td>
            <td className="cell-summary" title={obs.tool_input_summary}>
              {obs.tool_input_summary
                ? obs.tool_input_summary.length > 60
                  ? obs.tool_input_summary.substring(0, 60) + '...'
                  : obs.tool_input_summary
                : '—'}
            </td>
            <td className="cell-summary" title={obs.tool_output_summary}>
              {obs.tool_output_summary
                ? obs.tool_output_summary.length > 60
                  ? obs.tool_output_summary.substring(0, 60) + '...'
                  : obs.tool_output_summary
                : obs.success === null ? '—' : obs.success ? 'OK' : obs.error_message || 'Error'}
            </td>
            <td>{obs.success === null ? '—' : obs.success ? '✓' : '✗'}</td>
            <td>{new Date(obs.created_at * 1000).toLocaleString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
    <div className="evo-pagination">Total: {data.total}</div>
  </>
)}
```

- [ ] **Step 2: Commit**

```bash
cd /Users/mac/Documents/Projects/web-agent && git add frontend/src/pages/evolution/ObservationBrowser.tsx
git commit -m "feat: show tool input/output summaries in observation browser"
```

---

### Task 12: Frontend — CSS and i18n for new UI elements

**Files:**
- Modify: `frontend/src/pages/evolution/evolution.css`
- Modify: `frontend/src/i18n/zh.json`
- Modify: `frontend/src/i18n/en.json`

- [ ] **Step 1: Add new CSS rules**

Append to `evolution.css`:

```css
/* Time range selector */
.time-range-bar {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 16px;
}

.time-range-btn {
  padding: 6px 14px;
  border: 1px solid var(--border-color, #d1d5db);
  border-radius: 6px;
  background: var(--bg-surface, #fff);
  color: var(--text-primary, #374151);
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s;
}

.time-range-btn:hover {
  background: var(--bg-hover, #f3f4f6);
}

.time-range-btn.active {
  background: #4f46e5;
  color: #fff;
  border-color: #4f46e5;
}

.auto-refresh-indicator {
  margin-left: auto;
  font-size: 12px;
  color: #9ca3af;
}

/* Funnel window label */
.funnel-window-label {
  font-size: 11px;
  color: #9ca3af;
  text-align: center;
  margin-top: 8px;
}

/* Score badge */
.score-badge {
  font-weight: 600;
  font-size: 12px;
  padding: 1px 6px;
  border-radius: 4px;
}

.score-good {
  color: #166534;
  background: #dcfce7;
}

.score-warn {
  color: #92400e;
  background: #fef3c7;
}

.score-bad {
  color: #991b1b;
  background: #fee2e2;
}

.score-na {
  color: #9ca3af;
}

/* Signal baseline */
.signal-baseline {
  font-size: 11px;
  color: #9ca3af;
  margin-top: 4px;
}

/* Cell summary truncation */
.cell-summary {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}
```

- [ ] **Step 2: Add i18n keys**

In `frontend/src/i18n/zh.json`, add to `evolutionMonitor`:

```json
"timeToday": "今日",
"time7Days": "最近 7 天",
"time30Days": "最近 30 天",
"timeAll": "全部",
"autoRefresh": "每 30s 自动刷新",
"instinctCount": "本能数",
"compositeScore": "综合分数",
"daysActive": "活跃天数",
"inputSummary": "输入摘要",
"outputSummary": "输出/状态"
```

In `frontend/src/i18n/en.json`, add to `evolutionMonitor`:

```json
"timeToday": "Today",
"time7Days": "Last 7 Days",
"time30Days": "Last 30 Days",
"timeAll": "All",
"autoRefresh": "Auto-refresh 30s",
"instinctCount": "Instincts",
"compositeScore": "Score",
"daysActive": "Active",
"inputSummary": "Input",
"outputSummary": "Output"
```

- [ ] **Step 3: Commit**

```bash
cd /Users/mac/Documents/Projects/web-agent && git add frontend/src/pages/evolution/evolution.css frontend/src/i18n/zh.json frontend/src/i18n/en.json
git commit -m "feat: CSS and i18n for evolution dashboard improvements"
```

---

### Task 13: Run all tests and final verification

- [ ] **Step 1: Run backend tests**

```bash
cd /Users/mac/Documents/Projects/web-agent && uv run pytest tests/unit/test_evolution_api.py tests/unit/test_evolution_signals.py tests/unit/test_instinct_extractor.py tests/unit/test_observation.py tests/unit/test_evolution_log.py -v
```

Expected: All pass.

- [ ] **Step 2: Frontend type check**

```bash
cd /Users/mac/Documents/Projects/web-agent/frontend && npx tsc --noEmit
```

Expected: No errors.

- [ ] **Step 3: Manual verification checklist**

Start the dev server (`uv run uvicorn main_server:app --reload`) and:
- [ ] Visit `/evolution`, see time range buttons at top
- [ ] Click "最近 7 天" — stats and funnel update
- [ ] Verify funnel has time window label below it
- [ ] Evolution table shows 本能数, 综合分数, 活跃天数 columns
- [ ] Click an evolution row — detail page loads
- [ ] Signal breakdown shows baseline values with delta percentages
- [ ] Observations tab shows 输入摘要 and 输出/状态 columns
- [ ] Wait 30 seconds — data auto-refreshes (check network tab)
- [ ] Click into detail — auto-refresh pauses
- [ ] Go back to overview — auto-refresh resumes

- [ ] **Step 4: Final commit**

```bash
git commit -m "chore: final verification after dashboard improvements"
```

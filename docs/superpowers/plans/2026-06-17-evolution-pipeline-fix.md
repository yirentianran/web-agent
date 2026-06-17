# Evolution Pipeline Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the Evolution data pipeline bugs, enable L4 context injection so instincts affect agent behavior, and simplify by removing the broken snapshot precomputation system.

**Architecture:** Remove `skill_eval_snapshots` table + `EvolutionSignals` class entirely. Replace trend/signal display with real-time aggregation from `observations` table. Enable the commented-out L4 Semantic Context block in `build_system_prompt()`. Fix remaining bugs (diff path, unique_user_count, import casing).

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, React 18, TypeScript, Recharts

---

### Task 1: Remove snapshot-dependent methods from EvolutionLogStore

**Files:**
- Modify: `src/evolution_log.py`

- [ ] **Step 1: Delete snapshot methods and fix docstring**

Delete the entire "Snapshots" section (lines 125-170: `create_snapshot`, `get_snapshots`, `get_last_snapshots`) and the daily eval methods (lines 172-197: `get_active_evolutions`, `get_expired_reviews`). Update module docstring.

Open `src/evolution_log.py`, replace line 1:
```python
"""CRUD for evolution_log and skill_eval_snapshots tables."""
```
with:
```python
"""CRUD for evolution_log table."""
```

Delete lines 125-197 (from `# ── Snapshots ──` through `get_expired_reviews`).

- [ ] **Step 2: Verify file is clean**

Run: `uv run python -c "from src.evolution_log import EvolutionLogStore; print('OK')"`
Expected: OK (no import errors)

- [ ] **Step 3: Commit**

```bash
git add src/evolution_log.py
git commit -m "refactor: remove snapshot and daily eval methods from EvolutionLogStore"
```

---

### Task 2: Delete EvolutionSignals module

**Files:**
- Delete: `src/evolution_signals.py`
- Modify: `src/collective_intelligence.py`

- [ ] **Step 1: Delete the file**

```bash
rm src/evolution_signals.py
```

- [ ] **Step 2: Remove _daily_eval_loop from CollectiveIntelligenceEngine**

In `src/collective_intelligence.py`, delete the `_daily_eval_loop` method (lines 72-98). Remove the `# Loop 2: daily eval at 02:00` line (line 51) and the `asyncio.create_task(self._daily_eval_loop())` call (line 52).

Replace:
```python
        # Loop 1: instinct extraction every 10 minutes
        asyncio.create_task(self._extraction_loop())

        # Loop 2: daily eval at 02:00
        asyncio.create_task(self._daily_eval_loop())

        logger.info("Collective intelligence background jobs started")
```
with:
```python
        asyncio.create_task(self._extraction_loop())
        logger.info("Collective intelligence background jobs started")
```

Also delete the entire `_daily_eval_loop` method (lines 72-98):
```python
    async def _daily_eval_loop(self) -> None:
        import asyncio as _asyncio
        import time
        from src.evolution_signals import EvolutionSignals

        while True:
            now = time.localtime()
            seconds_until_0200 = (
                (24 - now.tm_hour - 2) % 24 * 3600
                - now.tm_min * 60
                - now.tm_sec
            )
            if seconds_until_0200 <= 0:
                seconds_until_0200 = 24 * 3600
            await _asyncio.sleep(seconds_until_0200)

            try:
                signals = EvolutionSignals(
                    self.db, self._evo_log_store, self._skill_manager
                )
                result = await signals.run_daily_eval()
                logger.info(
                    "Daily eval: %d evaluated, %d degraded, %d rolled back",
                    result["evaluated"], result["degraded"], result["rolled_back"],
                )
            except Exception as exc:
                logger.error("Daily eval failed: %s", exc)
```

- [ ] **Step 3: Verify**

Run: `uv run python -c "from src.collective_intelligence import CollectiveIntelligenceEngine; print('OK')"`
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add src/evolution_signals.py src/collective_intelligence.py
git commit -m "refactor: remove EvolutionSignals and daily eval loop"
```

---

### Task 3: Add database migration to drop skill_eval_snapshots

**Files:**
- Modify: `src/database.py`

- [ ] **Step 1: Add DROP TABLE migration**

In `src/database.py`, find the migration methods area (after the `_migrate_skill_usage` method around line 931). Add a new migration method:

```python
    async def _migrate_drop_eval_snapshots(self) -> None:
        """Drop skill_eval_snapshots table — replaced by real-time aggregation."""
        async with self.connection() as conn:
            await conn.execute("DROP TABLE IF EXISTS skill_eval_snapshots")
```

- [ ] **Step 2: Call the migration from _run_migrations**

Find `_run_migrations` method and add the call. Search for the last migration call (e.g., `await self._migrate_add_baseline_metrics()` around line 930). Add after it:

```python
        await self._migrate_drop_eval_snapshots()
```

- [ ] **Step 3: Remove table creation from initial schema**

In `_create_tables_evolution` (around line 745), delete the `skill_eval_snapshots` CREATE TABLE and its index:

Remove:
```sql
                CREATE TABLE IF NOT EXISTS skill_eval_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    evolution_log_id INTEGER NOT NULL REFERENCES evolution_log(id),
                    snapshot_date TEXT NOT NULL,
                    usage_count INTEGER DEFAULT 0,
                    unique_users INTEGER DEFAULT 0,
                    avg_rating REAL,
                    session_success_rate REAL,
                    composite_score REAL NOT NULL DEFAULT 0.0,
                    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
                );
```
and:
```sql
                CREATE INDEX IF NOT EXISTS idx_eval_snap_log ON skill_eval_snapshots(evolution_log_id);
```

- [ ] **Step 4: Verify**

Run: `uv run python -c "from src.database import Database; print('OK')"`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add src/database.py
git commit -m "refactor: drop skill_eval_snapshots table, add migration"
```

---

### Task 4: Fix evolution_overview endpoint (remove snapshot dependency)

**Files:**
- Modify: `main_server.py` (around line 6306)

- [ ] **Step 1: Remove snapshot composite_score subquery from overview**

The `evolution_overview` endpoint currently queries `skill_eval_snapshots` for `composite_score`. Replace the snapshot logic with a simple `None` (score can be added back later with real-time aggregation).

In `main_server.py`, find `evolution_overview` (line 6306). Replace lines 6330-6343:

Replace:
```python
            # Batch latest composite scores via correlated subquery
            snap_rows = await conn.execute_fetchall(
                f"""SELECT s.evolution_log_id, s.composite_score
                    FROM skill_eval_snapshots s
                    INNER JOIN (
                        SELECT evolution_log_id, MAX(snapshot_date) AS max_date
                        FROM skill_eval_snapshots
                        WHERE evolution_log_id IN ({placeholders})
                        GROUP BY evolution_log_id
                    ) latest ON s.evolution_log_id = latest.evolution_log_id
                    AND s.snapshot_date = latest.max_date""",
                item_ids,
            )
            snap_map = {r[0]: r[1] for r in snap_rows if r[1] is not None}
```

with:
```python
            # Composite score now computed real-time via /trend endpoint
            snap_map: dict[int, float] = {}
```

And update the loop below (line 6345-6350):
```python
    import time as _time
    now = _time.time()
    for item in result["items"]:
        item["instinct_count"] = instinct_map.get(item["id"], 0) if item_ids else 0
        score = snap_map.get(item["id"]) if item_ids else None
        item["composite_score"] = round(score, 4) if score is not None else None
        item["days_active"] = max(1, int((now - item["created_at"]) / 86400))
```
replace with:
```python
    import time as _time
    now = _time.time()
    for item in result["items"]:
        item["instinct_count"] = instinct_map.get(item["id"], 0) if item_ids else 0
        item["composite_score"] = None
        item["days_active"] = max(1, int((now - item["created_at"]) / 86400))
```

- [ ] **Step 2: Verify**

Run: `uv run python -c "from main_server import app; print('OK')" 2>&1 | tail -1`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add main_server.py
git commit -m "fix: remove snapshot dependency from evolution overview API"
```

---

### Task 5: Rewrite evolution_detail endpoint (no snapshots)

**Files:**
- Modify: `main_server.py` (around line 6368)

- [ ] **Step 1: Replace evolution_detail with simplified version**

Find the `evolution_detail` endpoint (line 6368). Replace lines 6368-6428 with:

```python
@app.get("/api/admin/evolution/{evolution_id}")
async def evolution_detail(
    evolution_id: int,
    current_user: str = Depends(require_admin),
):
    """Get evolution detail with linked instincts."""
    from src.evolution_log import EvolutionLogStore

    store = EvolutionLogStore(_db)
    log_with_instincts = await store.get_log_with_instincts(evolution_id)
    if not log_with_instincts:
        raise HTTPException(404, "Evolution record not found")

    return log_with_instincts
```

- [ ] **Step 2: Verify**

Run: `uv run python -c "from main_server import app; print('OK')" 2>&1 | tail -1`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add main_server.py
git commit -m "refactor: simplify evolution_detail endpoint, remove snapshot/signal data"
```

---

### Task 6: Add trend aggregation API endpoint

**Files:**
- Modify: `main_server.py`

- [ ] **Step 1: Add GET /api/admin/evolution/{id}/trend**

Add after the `evolution_detail` endpoint (after step 1's new code). Insert:

```python
@app.get("/api/admin/evolution/{evolution_id}/trend")
async def evolution_trend(
    evolution_id: int,
    days: int = 30,
    current_user: str = Depends(require_admin),
):
    """Real-time trend data aggregated from observations."""
    from src.evolution_log import EvolutionLogStore

    store = EvolutionLogStore(_db)
    log = await store.get_log(evolution_id)
    if not log:
        raise HTTPException(404, "Evolution record not found")

    cutoff = time.time() - days * 86400

    async with _db.connection() as conn:
        _ = await conn.execute("SELECT 1")  # ensure aiosqlite row factory
        rows = await conn.execute_fetchall(
            """SELECT
                   date(created_at, 'unixepoch') as day,
                   COUNT(*) as total,
                   SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success_count
               FROM observations
               WHERE created_at >= ?
                 AND event_type = 'tool_call_end'
                 AND success IS NOT NULL
               GROUP BY day
               ORDER BY day ASC""",
            (cutoff,),
        )

    trend = []
    for r in rows:
        total = r[1]
        success = r[2] or 0
        trend.append({
            "date": r[0],
            "success_rate": round(success / total, 4) if total > 0 else 1.0,
            "usage_count": total,
        })

    return trend
```

- [ ] **Step 2: Verify**

Run: `uv run python -c "from main_server import app; print('OK')" 2>&1 | tail -1`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add main_server.py
git commit -m "feat: add evolution trend API with real-time observation aggregation"
```

---

### Task 7: Add signals aggregation API endpoint

**Files:**
- Modify: `main_server.py`

- [ ] **Step 1: Add GET /api/admin/evolution/{id}/signals**

Append after the trend endpoint:

```python
@app.get("/api/admin/evolution/{evolution_id}/signals")
async def evolution_signals(
    evolution_id: int,
    current_user: str = Depends(require_admin),
):
    """Success rate and usage signals vs. baseline (first 7 days after creation)."""
    from src.evolution_log import EvolutionLogStore

    store = EvolutionLogStore(_db)
    log = await store.get_log(evolution_id)
    if not log:
        raise HTTPException(404, "Evolution record not found")

    now = time.time()
    baseline_end = log["created_at"] + 7 * 86400
    recent_start = now - 7 * 86400

    async with _db.connection() as conn:
        # Baseline: first 7 days after evolution creation
        bl_rows = await conn.execute_fetchall(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success_count
               FROM observations
               WHERE created_at >= ? AND created_at < ?
                 AND event_type = 'tool_call_end'
                 AND success IS NOT NULL""",
            (log["created_at"], baseline_end),
        )
        bl_total = bl_rows[0][0] if bl_rows else 0
        bl_success = bl_rows[0][1] or 0
        baseline_success_rate = round(bl_success / bl_total, 4) if bl_total > 0 else 1.0
        baseline_usage = round(bl_total / 7, 1)  # per day average

        # Current: last 7 days
        cur_rows = await conn.execute_fetchall(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success_count
               FROM observations
               WHERE created_at >= ?
                 AND event_type = 'tool_call_end'
                 AND success IS NOT NULL""",
            (recent_start,),
        )
        cur_total = cur_rows[0][0] if cur_rows else 0
        cur_success = cur_rows[0][1] or 0
        current_success_rate = round(cur_success / cur_total, 4) if cur_total > 0 else 1.0
        current_usage = round(cur_total / 7, 1)

    def _delta_pct(cur: float, base: float) -> float:
        if base == 0:
            return 100.0 if cur > 0 else 0.0
        return round((cur - base) / base * 100, 1)

    return {
        "success_rate": {
            "current": current_success_rate,
            "baseline": baseline_success_rate,
            "delta_pct": _delta_pct(current_success_rate, baseline_success_rate),
        },
        "usage_count": {
            "current": current_usage,
            "baseline": baseline_usage,
            "delta_pct": _delta_pct(current_usage, baseline_usage),
        },
    }
```

- [ ] **Step 2: Verify**

Run: `uv run python -c "from main_server import app; print('OK')" 2>&1 | tail -1`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add main_server.py
git commit -m "feat: add evolution signals API computing success_rate and usage vs baseline"
```

---

### Task 8: Fix version diff path

**Files:**
- Modify: `main_server.py` (line 6459)

- [ ] **Step 1: Fix the archive path**

In `evolution_diff` endpoint, line 6459, replace:

```python
        old_file = skill_dir / f"SKILL_v{from_ver}.md"
```

with:

```python
        old_file = skill_dir / "versions" / f"v{from_ver}" / "SKILL.md"
```

- [ ] **Step 2: Verify**

Run: `uv run python -c "from main_server import app; print('OK')" 2>&1 | tail -1`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add main_server.py
git commit -m "fix: use correct archive path for evolution version diff"
```

---

### Task 9: Enable L4 context injection in build_system_prompt

**Files:**
- Modify: `main_server.py`

- [ ] **Step 1: Add instinct loading helper function**

Add before `build_system_prompt` (around line 929):

```python
async def _load_instinct_context(user_message: str, db) -> str:
    """Return L4 learned patterns for the current query, or empty string."""
    try:
        async with db.connection() as conn:
            rows = await conn.execute_fetchall(
                """SELECT normalized_trigger, guidance FROM instincts
                   WHERE scope = 'active' AND confidence >= 0.5
                     AND guidance IS NOT NULL AND guidance != ''
                   ORDER BY confidence DESC
                   LIMIT 20"""
            )
    except Exception:
        return ""

    if not rows:
        return ""

    # Simple keyword match: score instincts by word overlap with user message
    query_words = set(user_message.lower().split())
    scored = []
    for trigger, guidance in rows:
        trigger_words = set((trigger or "").lower().split())
        if not trigger_words:
            continue
        overlap = len(query_words & trigger_words)
        if overlap > 0:
            scored.append((overlap, guidance))

    if not scored:
        return ""

    scored.sort(reverse=True)
    top = scored[:3]

    lines = [
        "\n## Learned Patterns",
        "The following patterns have been identified from past experience:",
        "",
    ]
    for _, guidance in top:
        lines.append(f"- {guidance}")
    return "\n".join(lines)
```

- [ ] **Step 2: Enable L4 block in build_system_prompt**

In `build_system_prompt`, replace lines 1085-1086:

```python
    # ── L4: Semantic Context — disabled, re-enable when data pipeline is ready ──

    # Final language enforcement — placed at the very end to leverage
```

with:

```python
    # Final language enforcement — placed at the very end to leverage
```

Then, change the function from sync to accept an optional `db` parameter. Add a new parameter `db=None` to `build_system_prompt` signature.

After line 1083 (`parts.append(build_file_generation_rules_prompt(workspace))`), add:

```python
    # ── L4: Learned Patterns from collective intelligence ──
    if db is not None:
        instinct_ctx = await _load_instinct_context(first_user_message, db)
        if instinct_ctx:
            parts.append(instinct_ctx)
```

Wait — `build_system_prompt` is currently synchronous. Making it async would cascade. Instead, let's pre-load the instinct context in the caller and pass it as a string parameter.

Better approach: Add a string parameter `instinct_context: str = ""` to `build_system_prompt`.

In `build_system_prompt` signature (line 930), change:

```python
def build_system_prompt(
    user_id: str, skills: dict[str, dict[str, Any]], workspace: Path | None = None, language: str | None = None
) -> str:
```

to:

```python
def build_system_prompt(
    user_id: str,
    skills: dict[str, dict[str, Any]],
    workspace: Path | None = None,
    language: str | None = None,
    instinct_context: str = "",
) -> str:
```

And replace lines 1085-1086:

```python
    # ── L4: Semantic Context — disabled, re-enable when data pipeline is ready ──

    # Final language enforcement — placed at the very end to leverage
```

with:

```python
    # ── L4: Learned Patterns from collective intelligence ──
    if instinct_context:
        parts.append(instinct_context)

    # Final language enforcement — placed at the very end to leverage
```

- [ ] **Step 3: Load instinct context in callers**

In both `_get_cached_system_prompt` (line 1100) and in the agent task setup, load instinct context and pass it.

In `_get_cached_system_prompt`, add a parameter `instinct_context: str = ""` and pass it through to `build_system_prompt`.

Find where `_get_cached_system_prompt` is called (in `run_agent_task` and `run_agent_task_container`). Add before the call:

```python
    instinct_ctx = await _load_instinct_context(user_message, _db) if _db is not None else ""
```

And pass `instinct_context=instinct_ctx` to both `_get_cached_system_prompt` and `build_system_prompt`.

- [ ] **Step 4: Verify**

Run: `uv run python -c "from main_server import app; print('OK')" 2>&1 | tail -1`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add main_server.py
git commit -m "feat: enable L4 instinct context injection in system prompt"
```

---

### Task 10: Fix unique_user_count not incrementing

**Files:**
- Modify: `src/instinct_extractor.py`

- [ ] **Step 1: Add user_id parameter and increment logic**

The `upsert` method needs to know which user triggered the event to count unique users. Add a `user_id: str = ""` parameter.

In `src/instinct_extractor.py`, change the `upsert` signature (around line 55):

```python
    async def upsert(
        self,
        *,
        domain: str,
        normalized_trigger: str,
        trigger: str,
        action: str,
        confidence: float = 0.3,
        evidence_json: str = "",
    ) -> int:
```

to:

```python
    async def upsert(
        self,
        *,
        domain: str,
        normalized_trigger: str,
        trigger: str,
        action: str,
        confidence: float = 0.3,
        evidence_json: str = "",
        user_id: str = "",
    ) -> int:
```

In the `if existing:` block (line 78), after setting `new_confidence`, add unique user tracking:

```python
            if existing:
                new_id = existing[0]
                new_source_count = existing[2] + 1
                new_confidence = min(0.9, existing[1] + 0.05)
                unique_delta = 0
                if user_id:
                    # Check if this user has already contributed to this instinct
                    user_check = await conn.execute_fetchall(
                        """SELECT 1 FROM observations o
                           JOIN instincts i ON i.id = ?
                           WHERE o.user_id = ?
                           AND o.event_type = 'tool_call_end'
                           LIMIT 1""",
                        (new_id, user_id),
                    )
                    if not user_check:
                        unique_delta = 1
                await conn.execute(
                    """UPDATE instincts
                       SET source_count = ?, confidence = ?, updated_at = ?,
                           unique_user_count = unique_user_count + ?
                       WHERE id = ?""",
                    (new_source_count, new_confidence, time.time(), unique_delta, new_id),
                )
```

And update the INSERT in the else branch to include `unique_user_count`:

```python
                cursor = await conn.execute(
                    """INSERT INTO instincts
                       (domain, normalized_trigger, trigger, action, confidence,
                        source_count, evidence_json, unique_user_count)
                       VALUES (?, ?, ?, ?, ?, 1, ?, 1)""",
                    (domain, normalized_trigger, trigger, action, confidence, evidence_json),
                )
```

- [ ] **Step 2: Pass user_id at call site**

Find where `instinct_store.upsert()` is called in `instinct_extractor.py` (around line 471). Pass `user_id` from the session context if available.

Search for the upsert call:
```python
await self.instinct_store.upsert(
    domain=...,
    ...
)
```

If session user_id is tracked, pass it. Otherwise pass an empty string (the upsert gracefully handles this).

- [ ] **Step 3: Verify**

Run: `uv run python -c "from src.instinct_extractor import InstinctStore; print('OK')"`
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add src/instinct_extractor.py
git commit -m "fix: increment unique_user_count when new users trigger an instinct"
```

---

### Task 11: Frontend — fix import case, remove RollbackTimeline and PipelineFunnel

**Files:**
- Modify: `frontend/src/App.tsx`
- Delete: `frontend/src/pages/evolution/RollbackTimeline.tsx`
- Delete: `frontend/src/pages/evolution/PipelineFunnel.tsx`
- Modify: `frontend/src/pages/evolutionpage.tsx`

- [ ] **Step 1: Fix case-sensitive import in App.tsx**

In `frontend/src/App.tsx` line 24, change:
```typescript
import EvolutionPage from "./pages/EvolutionPage";
```
to:
```typescript
import EvolutionPage from "./pages/evolutionpage";
```

- [ ] **Step 2: Delete RollbackTimeline and PipelineFunnel**

```bash
rm frontend/src/pages/evolution/RollbackTimeline.tsx
rm frontend/src/pages/evolution/PipelineFunnel.tsx
```

- [ ] **Step 3: Remove imports and references in evolutionpage.tsx**

In `frontend/src/pages/evolutionpage.tsx`, remove lines 5 and 7:
```typescript
import { PipelineFunnel } from './evolution/PipelineFunnel';
```
(Keep line 5 if it's StatsCards, delete only PipelineFunnel import)

Find and remove the `<PipelineFunnel>` JSX usage in the component render. Search for `PipelineFunnel` in the file and remove its usage.

- [ ] **Step 4: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No new errors (may have pre-existing ones)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/pages/evolutionpage.tsx frontend/src/pages/evolution/RollbackTimeline.tsx frontend/src/pages/evolution/PipelineFunnel.tsx
git commit -m "fix: remove RollbackTimeline, PipelineFunnel; fix import casing"
```

---

### Task 12: Frontend — update types and API hooks

**Files:**
- Modify: `frontend/src/hooks/useEvolutionApi.ts`

- [ ] **Step 1: Update types — remove Snapshot, add TrendPoint and new Signals**

In `useEvolutionApi.ts`, delete the `Snapshot` interface (lines 31-38). Add new types:

```typescript
export interface TrendPoint {
  date: string
  success_rate: number
  usage_count: number
}

export interface EvolutionSignals {
  success_rate: { current: number; baseline: number; delta_pct: number }
  usage_count: { current: number; baseline: number; delta_pct: number }
}
```

Update `EvolutionDetail` (lines 25-29) — remove `snapshots` and `signal_breakdown` from the extending interface, keep the rest:

```typescript
export interface EvolutionDetail extends EvolutionItem {
  instincts?: InstinctItem[]
}
```

Remove the `SignalBreakdown` interface (lines 40-44) — keep `EvolutionSignals` from above.

- [ ] **Step 2: Add fetchTrend and fetchSignals to EvolutionApi**

In `EvolutionApi` interface, add:

```typescript
  fetchTrend: (id: number, days?: number) => Promise<TrendPoint[]>
  fetchSignals: (id: number) => Promise<EvolutionSignals>
```

- [ ] **Step 3: Implement fetchTrend and fetchSignals in useEvolutionApi hook**

Add before the `refetch` useCallback:

```typescript
  const fetchTrend = useCallback(
    (id: number, days: number = 30): Promise<TrendPoint[]> =>
      fetchJson<TrendPoint[]>(`${API_BASE}/${id}/trend?days=${days}`),
    [],
  )

  const fetchSignals = useCallback(
    (id: number): Promise<EvolutionSignals> =>
      fetchJson<EvolutionSignals>(`${API_BASE}/${id}/signals`),
    [],
  )
```

Add them to the return `useMemo` object.

- [ ] **Step 4: Verify**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No new errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useEvolutionApi.ts
git commit -m "refactor: update evolution types and API hooks for trend/signals"
```

---

### Task 13: Frontend — update ScoreTrendChart and SignalBreakdown

**Files:**
- Modify: `frontend/src/pages/evolution/ScoreTrendChart.tsx`
- Modify: `frontend/src/pages/evolution/SignalBreakdown.tsx`
- Modify: `frontend/src/pages/evolution/EvolutionDetail.tsx`

- [ ] **Step 1: Rewrite ScoreTrendChart for TrendPoint data**

Replace the entire `ScoreTrendChart.tsx`:

```typescript
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts'
import type { TrendPoint } from '../../hooks/useEvolutionApi'

interface Props {
  data: TrendPoint[]
}

export default function ScoreTrendChart({ data }: Props) {
  const chartData = data.map((d) => ({
    date: d.date,
    successRate: d.success_rate * 100,
    usage: d.usage_count,
  }))

  return (
    <div className="evo-chart">
      <h4>Success Rate Trend</h4>
      <ResponsiveContainer width="100%" height={250}>
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="date" />
          <YAxis yAxisId="left" domain={[0, 100]} />
          <YAxis yAxisId="right" orientation="right" />
          <Tooltip />
          <Line
            yAxisId="left"
            type="monotone"
            dataKey="successRate"
            stroke="#3b82f6"
            name="Success Rate %"
            dot={false}
          />
          <Line
            yAxisId="right"
            type="monotone"
            dataKey="usage"
            stroke="#10b981"
            name="Usage Count"
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
```

- [ ] **Step 2: Rewrite SignalBreakdown for 2-signal model**

Replace the entire `SignalBreakdown.tsx`:

```typescript
import type { EvolutionSignals } from '../../hooks/useEvolutionApi'

interface Props {
  signals: EvolutionSignals
}

function DeltaIndicator({ deltaPct }: { deltaPct: number }) {
  const isPositive = deltaPct >= 0
  return (
    <div className={`signal-delta ${isPositive ? 'positive' : 'negative'}`}>
      {isPositive ? '↑' : '↓'} {Math.abs(deltaPct).toFixed(1)}%
    </div>
  )
}

export default function SignalBreakdown({ signals }: Props) {
  return (
    <div className="signal-breakdown">
      <div className="signal-card">
        <h5>Tool Success Rate</h5>
        <div className="signal-value">
          {(signals.success_rate.current * 100).toFixed(1)}%
        </div>
        <DeltaIndicator deltaPct={signals.success_rate.delta_pct} />
        <div className="signal-baseline">
          Baseline: {(signals.success_rate.baseline * 100).toFixed(1)}%
        </div>
      </div>
      <div className="signal-card">
        <h5>Usage</h5>
        <div className="signal-value">{signals.usage_count.current} / day</div>
        <DeltaIndicator deltaPct={signals.usage_count.delta_pct} />
        <div className="signal-baseline">
          Baseline: {signals.usage_count.baseline} / day
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Update EvolutionDetail to use new data sources**

Replace `EvolutionDetail.tsx`:

1. Remove `import RollbackTimeline from './RollbackTimeline'` (line 9)
2. Change imports of `ScoreTrendChart` and `SignalBreakdown` to match new props
3. Add `TrendPoint` and `EvolutionSignals` to imports from `useEvolutionApi`
4. Add state for trend data and signals:

```typescript
  const [trend, setTrend] = useState<TrendPoint[]>([])
  const [signals, setSignals] = useState<EvolutionSignals | null>(null)
```

5. Load trend and signals in useEffect alongside detail:

```typescript
  api.fetchTrend(evolutionId).then(setTrend).catch(() => setTrend([]))
  api.fetchSignals(evolutionId).then(setSignals).catch(() => setSignals(null))
```

6. Replace the snapshot-based rendering block (lines 108-118) with:

```typescript
      {trend.length > 0 && (
        <>
          <ScoreTrendChart data={trend} />
          {signals && <SignalBreakdown signals={signals} />}
        </>
      )}
```

7. Remove `<RollbackTimeline evolutionId={evolutionId} />` (line 160)

- [ ] **Step 4: Verify**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No new errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/evolution/ScoreTrendChart.tsx frontend/src/pages/evolution/SignalBreakdown.tsx frontend/src/pages/evolution/EvolutionDetail.tsx
git commit -m "refactor: update evolution chart components for real-time aggregation"
```

---

### Task 14: Backend tests

**Files:**
- Create: `tests/unit/test_evolution_api.py`

- [ ] **Step 1: Write test file**

Create `tests/unit/test_evolution_api.py`:

```python
"""Tests for evolution trend, signals APIs, and L4 instinct context."""
import pytest
from unittest.mock import AsyncMock, MagicMock


class TestTrendAggregation:
    """Unit tests for trend data computation logic."""

    def test_aggregates_rows_into_trend_points(self):
        rows = [
            ("2026-06-17", 10, 8),
            ("2026-06-18", 5, 5),
        ]
        trend = []
        for r in rows:
            total = r[1]
            success = r[2] or 0
            trend.append({
                "date": r[0],
                "success_rate": round(success / total, 4) if total > 0 else 1.0,
                "usage_count": total,
            })
        assert trend[0] == {"date": "2026-06-17", "success_rate": 0.8, "usage_count": 10}
        assert trend[1] == {"date": "2026-06-18", "success_rate": 1.0, "usage_count": 5}

    def test_zero_total_returns_success_rate_1(self):
        rows = [("2026-06-17", 0, 0)]
        trend = []
        for r in rows:
            total = r[1]
            success = r[2] or 0
            trend.append({
                "date": r[0],
                "success_rate": round(success / total, 4) if total > 0 else 1.0,
                "usage_count": total,
            })
        assert trend[0]["success_rate"] == 1.0


class TestSignalsDeltaPct:
    """Unit tests for delta percentage computation."""

    def _delta_pct(self, cur, base):
        if base == 0:
            return 100.0 if cur > 0 else 0.0
        return round((cur - base) / base * 100, 1)

    def test_positive_delta(self):
        assert self._delta_pct(0.85, 0.80) == 6.2

    def test_no_change(self):
        assert self._delta_pct(0.5, 0.5) == 0.0

    def test_zero_both(self):
        assert self._delta_pct(0, 0) == 0.0

    def test_from_zero_baseline_with_usage(self):
        assert self._delta_pct(5, 0) == 100.0

    def test_negative_delta(self):
        assert self._delta_pct(0.7, 1.0) == -30.0


class TestL4InstinctContext:
    """Tests for _load_instinct_context helper."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_db_is_none(self):
        from main_server import _load_instinct_context
        result = await _load_instinct_context("test query", None)
        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_keyword_match(self):
        mock_db = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)
        mock_conn.execute_fetchall = AsyncMock(return_value=[
            ("trigger_word", "Do X when Y"),
        ])
        mock_db.connection.return_value = mock_conn

        from main_server import _load_instinct_context
        result = await _load_instinct_context("completely unrelated words", mock_db)
        assert result == ""

    @pytest.mark.asyncio
    async def test_matches_keywords_and_formats_context(self):
        mock_db = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)
        mock_conn.execute_fetchall = AsyncMock(return_value=[
            ("use python data", "Prefer Python for data processing"),
        ])
        mock_db.connection.return_value = mock_conn

        from main_server import _load_instinct_context
        result = await _load_instinct_context("process data with python", mock_db)
        assert "## Learned Patterns" in result
        assert "Prefer Python for data processing" in result

    @pytest.mark.asyncio
    async def test_limits_to_top_3_instincts(self):
        mock_db = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)
        mock_conn.execute_fetchall = AsyncMock(return_value=[
            ("python", "Guidance A"),
            ("python", "Guidance B"),
            ("python", "Guidance C"),
            ("python", "Guidance D"),
            ("python", "Guidance E"),
        ])
        mock_db.connection.return_value = mock_conn

        from main_server import _load_instinct_context
        result = await _load_instinct_context("python", mock_db)
        # Should only contain 3 guidance items (4 dashes = header + 3 items)
        assert result.count("- ") == 3

    @pytest.mark.asyncio
    async def test_handles_db_exception_gracefully(self):
        mock_db = MagicMock()
        mock_db.connection.side_effect = Exception("DB down")

        from main_server import _load_instinct_context
        result = await _load_instinct_context("test query", mock_db)
        assert result == ""
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/unit/test_evolution_api.py -v --tb=short
```
Expected: 9 tests pass

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_evolution_api.py
git commit -m "test: add unit tests for evolution trend, signals, and L4 context"
```

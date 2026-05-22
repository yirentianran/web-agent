# Collective Intelligence — Evolution Evaluation & Admin Dashboard

## Context

The web-agent Collective Intelligence system has four background loops (wiki mining, pattern extraction, auto-promotion, auto-evolution) with a five-tier AutoEvolvePolicy (APPLY_EDITS → AUTO_FIX → PROPOSE → REQUIRE_REVIEW → SKIP). However, there is **no post-evolution verification** — the system auto-evolves skills but never checks whether the evolution actually improved or degraded skill quality. Evolution results are only passively visible through the next cycle's feedback aggregation, which may take weeks.

This spec adds: (1) an automated evaluation/verification system, (2) semi-automatic rollback for degraded evolutions, and (3) an admin dashboard for monitoring the CI evolution lifecycle.

## Scope

- **Backend**: 3 new modules (`evolution_log.py`, `evolution_evaluator.py`, `evolution_rollback.py`), 5 new admin APIs, evolution policy adjustments
- **Frontend**: 1 new page at `/dashboard/evolution`, 6 new components, Monaco DiffEditor dependency
- **Database**: 2 new tables (`evolution_log`, `skill_eval_snapshots`)
- **Not in scope**: Real-time WebSocket push, external notification (Slack/email), multi-admin approval workflows

---

## 1. Data Model

### `evolution_log`

```sql
CREATE TABLE evolution_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name TEXT NOT NULL,
    from_version TEXT NOT NULL,
    to_version TEXT NOT NULL,
    evolve_policy TEXT NOT NULL,          -- APPLY_EDITS / AUTO_FIX / PROPOSE / MANUAL
    evolve_reason TEXT,
    status TEXT NOT NULL DEFAULT 'active', -- active / under_review / rolled_back / superseded
    created_at INTEGER NOT NULL,          -- unix timestamp
    reviewed_at INTEGER,
    reviewed_by TEXT,
    review_decision TEXT,                 -- kept / rolled_back
    auto_rollback_at INTEGER,            -- 48h timeout for auto-rollback
    rolledback_at INTEGER,
    rollback_reason TEXT
);
```

### `skill_eval_snapshots`

```sql
CREATE TABLE skill_eval_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evolution_log_id INTEGER NOT NULL REFERENCES evolution_log(id),
    snapshot_date TEXT NOT NULL,          -- YYYY-MM-DD
    usage_count INTEGER DEFAULT 0,
    unique_users INTEGER DEFAULT 0,
    avg_rating REAL,                      -- 0-5
    session_success_rate REAL,            -- 0-1
    composite_score REAL,                 -- weighted: 0.4*rating + 0.3*usage + 0.3*success
    created_at INTEGER NOT NULL
);
```

### Composite Score Formula

```python
composite = 0.4 * (avg_rating / 5.0) + 0.3 * usage_trend_ratio + 0.3 * session_success_rate
```

Where `usage_trend_ratio = current_daily_usage / baseline_daily_usage` (capped at 1.0). Baseline = average daily usage over the 7 days before evolution.

### Degradation Trigger

When `composite_score` stays below baseline (pre-evolution 7-day average) for **7 consecutive days**, status transitions to `under_review`.

---

## 2. Backend Architecture

### New Modules

| Module | Responsibility | Called by |
|--------|---------------|-----------|
| `src/evolution_log.py` | CRUD for `evolution_log` + `skill_eval_snapshots` | evaluator, rollback, APIs |
| `src/evolution_evaluator.py` | Daily snapshot generation, composite scoring, degradation detection | CI scheduler, APIs |
| `src/evolution_rollback.py` | Rollback state machine, 48h auto-rollback timer | evaluator, APIs |

### Modified Modules

**`src/collective_intelligence.py`** — Changes:

1. `_auto_evolve_loop()`: after successful evolution, write to `evolution_log` (status=active), trigger baseline snapshot
2. New `_eval_snapshot_loop()`: daily at 02:00 local time — snapshot all active/under_review records, check degradation conditions
3. New loop registration in `start_background_loops()`

**`src/auto_evolve.py`** — Policy adjustments:

| Change | Detail |
|--------|--------|
| Cooldown period | 7 days — skip if skill was evolved within cooldown |
| AUTO_FIX confidence gate | Haiku self-scores its fix (1-10), <6 demoted to PROPOSE |
| HIGH_USAGE_THRESHOLD | Raised from 50 to 100 |
| New priority order | APPLY_EDITS → AUTO_FIX (high conf) → PROPOSE → REQUIRE_REVIEW (>100 uses + low-conf auto) → SKIP |

### State Machine

```
         auto_evolve triggers
              │
              ▼
         ┌─────────┐
    ┌───▶│ active  │◀──── kept (admin decision)
    │    └────┬────┘
    │         │ 7 consecutive days composite < baseline
    │         ▼
    │    ┌──────────────┐
    │    │ under_review │
    │    └──┬────────┬──┘
    │       │        │ 48h timeout, no admin action
    │       │        ▼
    │       │   ┌────────────┐
    │       │   │ rolled_back│
    │       │   └────────────┘
    │       │ admin manually triggers rollback
    │       ▼
    │  ┌────────────┐
    └──│ rolled_back│
       └────────────┘
```

### New API Endpoints

All require `Depends(require_admin)`.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/admin/evolution/overview` | GET | List evolution records, filter by `?status=`, paginated |
| `/api/admin/evolution/{id}` | GET | Single evolution detail + snapshot trends |
| `/api/admin/evolution/{id}/diff` | GET | SKILL.md diff (from_version → to_version) |
| `/api/admin/evolution/{id}/review` | POST | Admin decision: `{"decision": "keep" \| "rollback"}` |
| `/api/admin/evolution/{id}/rollback` | POST | Manual rollback (bypasses 48h wait) |

Response shapes:

```json
// GET /overview
{
  "items": [
    {
      "id": 1,
      "skill_name": "code-reviewer",
      "from_version": "1.2",
      "to_version": "1.3",
      "evolve_policy": "AUTO_FIX",
      "status": "active",
      "composite_score": 0.72,
      "created_at": 1716307200,
      "days_active": 3
    }
  ],
  "total": 42,
  "page": 1,
  "page_size": 20
}

// GET /{id}
{
  "id": 1,
  "skill_name": "code-reviewer",
  "from_version": "1.2",
  "to_version": "1.3",
  "evolve_policy": "AUTO_FIX",
  "evolve_reason": "Specific bugs: timeout, error",
  "status": "under_review",
  "created_at": 1716307200,
  "auto_rollback_at": 1716739200,
  "rollback_timeline": [
    {"date": "2026-05-22T10:00", "event": "Auto-evolve triggered (AUTO_FIX)"},
    {"date": "2026-05-23T02:00", "event": "First snapshot: composite 0.52"},
    {"date": "2026-05-29T02:00", "event": "7 days below baseline → under_review"}
  ],
  "snapshots": [
    {"date": "2026-05-23", "usage_count": 34, "unique_users": 8, "avg_rating": 3.2, "session_success_rate": 0.78, "composite_score": 0.52}
  ],
  "signal_breakdown": {
    "rating": {"current": 3.2, "baseline": 4.1, "delta_pct": -22},
    "usage": {"current": 34, "baseline": 52, "delta_pct": -35},
    "session_success": {"current": 0.78, "baseline": 0.84, "delta_pct": -6}
  }
}

// GET /{id}/diff
{
  "from_version": "1.2",
  "to_version": "1.3",
  "diff": "--- a/code-reviewer/SKILL.md\n+++ b/code-reviewer/SKILL.md\n@@ -12,7 +12,7 @@\n-使用 Sonnet 4.6 进行审查\n+使用 Opus 4.7 进行审查"
}
```

---

## 3. Frontend

### Routing

New route `/dashboard/evolution` in `App.tsx`, admin-only guard. SettingsMenu entry: "CI Evolution" visible only when `userRole === "admin"`.

### Data Hook

`frontend/src/hooks/useEvolutionApi.ts` — follows existing `useDashboardApi.ts` patterns (AsyncState<T>, fetchJson, authToken from localStorage).

### Component Tree

```
EvolutionPage
├── EvolutionOverview        — filterable table (All/Active/Under Review/Rolled Back) + pagination
└── EvolutionDetail          — routed via :id param
    ├── ScoreTrendChart      — Recharts line chart: baseline vs current composite score
    ├── SignalBreakdown      — 3 metric cards: rating / usage / session success with delta %
    ├── VersionDiff           — Monaco DiffEditor, readOnly, side-by-side markdown diff
    └── RollbackTimeline     — vertical timeline of evolution events
```

### Dependencies

`@monaco-editor/react` — Monaco DiffEditor loaded from CDN on demand (not bundled into main chunk). Admin-only page, so 5MB CDN load is acceptable.

Page layout follows the existing dashboard design language (Recharts charts, tabbed filter bar, CSS modules in `frontend/src/components/dashboard/`).

---

## 4. Evolution Policy Adjustments

Applied to `src/auto_evolve.py`:

1. **Cooldown period (7 days)**: Check `evolution_log` for the skill's last evolution; skip if within cooldown. Configurable via `EVOLVE_COOLDOWN_DAYS`.

2. **AUTO_FIX confidence gate**: After Haiku generates the fix, Haiku self-scores the fix quality (1-10). Score ≥ 6 → AUTO_FIX; score < 6 → demote to PROPOSE.

3. **HIGH_USAGE_THRESHOLD**: Raised from 50 to 100. High-usage skills with high-confidence AUTO_FIX or APPLY_EDITS still proceed automatically (rollback safety net in place).

4. **Adjusted priority order**:

```
APPLY_EDITS     → always direct merge (evaluation safety net catches regressions)
AUTO_FIX        → confidence ≥ 6: auto-fix; < 6: demote to PROPOSE
PROPOSE         → generate suggestion, admin approval required before apply
REQUIRE_REVIEW  → usage > 100 AND not high-confidence auto strategy
SKIP            → cooldown active / insufficient signal
```

---

## 5. Testing Strategy

### Backend Tests (`tests/unit/test_evolution_evaluator.py`)

- Composite score calculation correctness (known inputs → expected output)
- Degradation detection: 7 consecutive below-baseline days triggers `under_review`
- Non-degradation: scores equal to baseline do NOT trigger
- Snapshot generation produces correct structure
- Rollback state machine: active → under_review → rolled_back transitions
- 48h auto-rollback timer calculation
- Cooldown period: skill evolved within 7 days → SKIP

### Backend Tests (`tests/unit/test_evolution_api.py`)

- All 5 endpoints return 401 without admin token
- Overview pagination and status filtering
- Detail endpoint includes all expected fields
- Diff endpoint returns valid diff string
- Review POST validates `decision` enum
- Rollback POST creates rollback_log entry

### Frontend Tests

- EvolutionPage renders with mocked API
- EvolutionOverview table renders rows with status badges
- EvolutionDetail renders ScoreTrendChart, SignalBreakdown, VersionDiff, RollbackTimeline
- Monaco DiffEditor lazy-loads without error
- Keep/Rollback buttons call API and redirect on success

---

## 6. Verification

1. **DB migration**: Run `CREATE TABLE` for `evolution_log` and `skill_eval_snapshots`, verify tables exist
2. **Policy cooldown**: Trigger auto-evolve twice for the same skill within 7 days → second attempt SKIPs
3. **Snapshot generation**: Run `_eval_snapshot_loop()` → verify snapshots created for all active evolutions
4. **Degradation → under_review**: Manually insert 7 low-score snapshots → next loop run transitions to `under_review`
5. **Auto-rollback**: Set `auto_rollback_at` in the past → next loop run executes rollback
6. **API integration**: `curl /api/admin/evolution/overview` with admin token returns paginated list
7. **UI**: Navigate to `/dashboard/evolution`, verify table renders, click row → detail page with charts
8. **Diff**: Click into a skill with version diff → Monaco DiffEditor shows side-by-side markdown diff
9. **Admin review**: POST `{"decision": "keep"}` → status transitions back to `active`

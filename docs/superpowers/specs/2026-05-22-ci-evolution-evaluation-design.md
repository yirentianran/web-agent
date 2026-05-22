# Collective Intelligence — Evolution Evaluation & Admin Dashboard

## Context

The current auto-evolve system uses a complex pipeline: 4h background loop → SQL aggregation → keyword matching (`BUG_KEYWORDS`, `VAGUE_KEYWORDS`) → five-tier policy → blind Haiku fix. The data source (`skill_feedback` table) is thin — short ratings and comments without conversation context. There is no post-evolution verification.

ECC's `continuous-learning` skill does this simpler: a Stop hook at session end → Claude reads the full transcript → extracts patterns → writes new skill files. The code is ~50 lines of bash + a prompt. The LLM does all the reasoning.

This spec replaces the complex pipeline with an ECC-inspired approach: **session-end trigger + Haiku reads full conversation + confidence-based auto-apply**.

## Scope

- **Replace** keyword-based `auto_evolve.py` pipeline with `session_learner.py`
- **Add** evaluation + semi-auto rollback for all evolutions
- **Add** admin dashboard at `/dashboard/evolution`
- **Keep** wiki mining, pattern extraction, auto-promotion loops unchanged

---

## 1. Core: Session-Based Evolution

### Trigger

Session ends in `main_server.py` → fire-and-forget call to `session_learner.analyze_session(session_id)`.

### Data

Query from existing DB tables (no new tables needed for this step):

```sql
-- Full conversation
SELECT seq, type, name, content FROM messages WHERE session_id = ? ORDER BY seq

-- Skills invoked in this session
SELECT skill_name FROM skill_usage WHERE session_id = ?

-- Existing feedback for those skills
SELECT skill_name, rating, comment FROM skill_feedback WHERE skill_name IN (...)
```

### Haiku Analysis

Send the above data to Haiku with this prompt:

```
Analyze this AI agent session and identify what we can learn.

## Session Messages
{formatted messages}

## Skills Used
{skill names}

## Existing Feedback for These Skills
{ratings and comments}

## Tasks
1. For each skill used: did it perform well? If not, what went wrong and how should SKILL.md change?
2. Did the user demonstrate any reusable workflow that could become a new skill?

Return JSON:
{
  "improvements": [
    {"skill_name": "...", "confidence": 1-10, "issue": "...", "suggested_fix": "fixed SKILL.md content"}
  ],
  "new_patterns": [
    {"name": "kebab-case-name", "confidence": 1-10, "description": "...", "skill_content": "SKILL.md content"}
  ]
}
```

### Confidence-Based Action

| Confidence | Action |
|-----------|--------|
| &ge; 7 | Auto-apply: modify SKILL.md / create new skill, register in DB, write `evolution_log` status=active, bump shared-skills generation |
| 4-6 | Propose: save suggestion, admin reviews in dashboard |
| < 4 | Discard |

No keyword matching. No five-tier policy. Haiku makes the call.

### Skill Registration (new skill only)

When `session_learner` creates a new skill from a `new_pattern`, it must make the skill discoverable:

1. **Write `SKILL.md`** to `shared-skills/{skill-name}/`
2. **Write `skill-meta.json`** alongside it (`source: "learned"`, `owner: "system"`)
3. **Register in DB** via `SkillManager.register_skill(skill_name, source="learned", ...)` — immediate, not via async scan
4. **Bump generation** via `_bump_shared_skills_gen()` — triggers `_sync_shared_skills()` for all users on their next session

This ensures the new skill is immediately:
- Discoverable by `load_skills()` (disk scan with `iterdir()`)
- Queryable via DB `skills` table (admin dashboard, search)
- Synced to user workspaces (`workspace/.claude/skills/` → SDK reads)

---

## 2. Evaluation & Rollback

### New Tables

```sql
CREATE TABLE evolution_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name TEXT NOT NULL,
    from_version TEXT NOT NULL,
    to_version TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'session_learner',  -- session_learner / manual / feedback
    evolve_reason TEXT,
    status TEXT NOT NULL DEFAULT 'active',           -- active / under_review / rolled_back
    created_at INTEGER NOT NULL,
    reviewed_at INTEGER,
    reviewed_by TEXT,
    review_decision TEXT,                            -- kept / rolled_back
    auto_rollback_at INTEGER                         -- 48h after under_review
);

CREATE TABLE skill_eval_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evolution_log_id INTEGER NOT NULL REFERENCES evolution_log(id),
    snapshot_date TEXT NOT NULL,
    usage_count INTEGER DEFAULT 0,
    unique_users INTEGER DEFAULT 0,
    avg_rating REAL,
    session_success_rate REAL,
    composite_score REAL,     -- 0.4*(rating/5.0) + 0.3*usage_ratio + 0.3*success_rate
    created_at INTEGER NOT NULL
);
```

### Composite Score

```python
composite = 0.4 * (avg_rating / 5.0) + 0.3 * usage_trend_ratio + 0.3 * session_success_rate
```

Where `usage_trend_ratio = current_daily / baseline_daily` (capped at 1.0). Baseline = 7 days before evolution.

### State Machine

```
session_learner (confidence >= 7)
        │
        ▼
   ┌─────────┐
┌─▶│ active  │◀─── kept (admin)
│  └────┬────┘
│       │ 7 days composite < baseline
│       ▼
│  ┌──────────────┐
│  │ under_review │
│  └──┬────────┬──┘
│     │        │ 48h no action → auto-rollback
│     │        ▼
│     │   ┌────────────┐
│     │   │ rolled_back│
│     │   └────────────┘
│     │ admin rollback
│     ▼
│  ┌────────────┐
└──│ rolled_back│
   └────────────┘
```

### Daily Evaluation Task

A simple daily task (02:00) in `collective_intelligence.py`:

```python
async def _eval_snapshot_loop():
    for log in get_active_evolutions():
        snap = compute_snapshot(log)
        save_snapshot(snap)
        last_7 = get_last_7_snapshots(log.id)
        if all(s.composite < log.baseline for s in last_7):
            mark_under_review(log)  # sets auto_rollback_at = now + 48h

    # Check expired under_review
    for log in get_expired_reviews():  # auto_rollback_at < now
        execute_rollback(log)
```

---

## 3. Admin APIs

All require `Depends(require_admin)`. Same as before:

| Endpoint | Purpose |
|----------|---------|
| `GET /api/admin/evolution/overview` | List all evolutions, filter `?status=`, paginated |
| `GET /api/admin/evolution/{id}` | Detail: version info + snapshot trend + signal breakdown |
| `GET /api/admin/evolution/{id}/diff` | SKILL.md diff (from → to) |
| `POST /api/admin/evolution/{id}/review` | Admin decision: `{"decision": "keep" \| "rollback"}` |

---

## 4. Admin Dashboard

Route: `/dashboard/evolution`. Two views:

### Overview Table

Filterable by status (All / Active / Under Review / Rolled Back). Columns: skill name, version, source, composite score, status badge, days active.

### Detail Page (click row)

- **ScoreTrendChart** — Recharts line chart: composite score over time vs baseline
- **SignalBreakdown** — 3 cards: user rating delta%, usage delta%, session success delta%
- **VersionDiff** — Monaco DiffEditor, read-only, side-by-side markdown
- **Keep / Rollback buttons** — for under_review items

Monaco loaded from CDN on demand (`@monaco-editor/react`), admin-only.

---

## 5. Files

### New

| File | Purpose |
|------|---------|
| `src/session_learner.py` | Session-end trigger, DB query, Haiku prompt, confidence-based apply |
| `src/evolution_evaluator.py` | Daily snapshot, composite scoring, degradation detection |
| `src/evolution_rollback.py` | Rollback state machine, 48h auto-rollback |
| `tests/unit/test_session_learner.py` | Session analysis + Haiku response handling |
| `tests/unit/test_evolution_evaluator.py` | Scoring + degradation logic |
| `frontend/src/pages/EvolutionPage.tsx` | Main page with overview table and detail view |
| `frontend/src/hooks/useEvolutionApi.ts` | Data fetching hook |

### Modified

| File | Change |
|------|--------|
| `main_server.py` | Session-end → call `session_learner.analyze_session()` |
| `src/collective_intelligence.py` | Add `_eval_snapshot_loop()`, simplify `_auto_evolve_loop()` |
| `src/database.py` | Add `evolution_log` + `skill_eval_snapshots` tables |
| `frontend/src/App.tsx` | Add `/dashboard/evolution` route |

### Deprecated

| File | Reason |
|------|--------|
| `src/auto_evolve.py` | Replaced by `session_learner.py` — keyword matching and five-tier policy no longer needed |
| `src/skill_feedback.py` `auto_fix_skill()` / `apply_user_edits()` | Replaced by session_learner's confidence-based apply |

---

## 6. OS & Mode Compatibility

### Container vs Non-Container

`session_learner` runs in `main_server.py` on the **host side** for both modes. The session-end hook is added identically to `run_agent_task()` (non-container) and `run_agent_task_container()` (container). Both paths have access to `_db`, `DATA_ROOT`, `_skill_manager`, and `_bump_shared_skills_gen`.

`agent_server.py` (container process) has no session lifecycle logic and is unaffected.

### Circular Import Avoidance

`session_learner.py` lives in `src/` and must not import from `main_server.py`. Instead, `SkillManager` and `_bump_shared_skills_gen` are injected via constructor:

```python
learner = SessionLearner(
    _db, DATA_ROOT,
    skill_manager=_skill_manager,        # for register_skill()
    on_skill_changed=_bump_shared_skills_gen,  # for sync trigger
)
```

### macOS / Linux

- `pathlib.Path` used for all path construction — cross-platform
- `_sync_shared_skills()` uses symlinks — learned skills appear instantly in all user workspaces
- Gen bump on skill creation triggers re-sync on next `_build_sdk_config()` call

### Windows

- `_sync_shared_skills()` uses directory copies + `.shared_skill_source` marker files instead of symlinks
- Gen bump is critical: without it, `_sync_shared_skills()` returns early (generation cache), and Windows users never get the updated/copied skill
- `platform.system()` detection is already built into `_sync_shared_skills()` — no changes needed
- `skill-meta.json` written alongside SKILL.md ensures `migrate_from_filesystem()` can read metadata on Windows

---

## 7. Comparison: Before vs After

| | Before | After |
|---|--------|-------|
| Trigger | 4h background loop | Session end |
| Data | `skill_feedback` (ratings + short comments) | `messages` table (full conversation) |
| Analysis | Keyword matching → five-tier policy | Haiku reads context → confidence score |
| "How to fix" | Haiku blind-fixes from bug keywords | Haiku sees actual errors and user corrections |
| Evolution direction | Vertical only (modify SKILL.md) | Vertical + horizontal (create new skills) |
| Evaluation | None | Daily snapshots + composite score + rollback |
| Code complexity | `auto_evolve.py` (246 lines) + `skill_feedback.py` evolution methods (~100 lines) | `session_learner.py` (~200 lines) |

---

## 8. Verification

1. Session with skill usage ends → `session_learner` runs → check `evolution_log` for new entry
2. Low-confidence finding → appears in admin dashboard as "proposed" (not auto-applied)
3. New pattern from session → new `SKILL.md` in `data/shared-skills/{skill-name}/` + DB registered + synced to user workspace
4. Insert 7 low-score snapshots → status transitions to `under_review`
5. `under_review` + 48h no action → auto-rollback executes
6. Admin POST `{"decision": "keep"}` → status returns to `active`
7. Navigate `/dashboard/evolution` → table renders, click row → detail with charts and diff

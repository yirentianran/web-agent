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

### Truncation

Sessions can exceed Haiku's context window. Before building the prompt:

1. Cap messages at **last 200** (user and assistant messages, filters out verbose system/tool-result where possible)
2. Truncate each message content to **2000 chars** max
3. If session has 0 skill usage records, skip analysis entirely

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

### API Call

Follow `main_server.py` title-generation pattern (not `skill_feedback.py` hardcoded pattern):

```python
import httpx

model = os.getenv("MODEL", "claude-haiku-4-5-20251001")
base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
api_key = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")

async with httpx.AsyncClient(timeout=120) as client:
    resp = await client.post(
        f"{base_url}/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        json={
            "model": model,
            "max_tokens": 4000,
            "system": "You analyze AI agent sessions to improve skills. Return ONLY valid JSON.",
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    resp.raise_for_status()
    data = resp.json()
    return json.loads(data["content"][0]["text"])
```

Key differences from `skill_feedback.py` pattern:
- Uses `ANTHROPIC_BASE_URL` env (respects proxies / private deployments)
- Uses `MODEL` env (allows model override)
- Has `system` parameter (sets context)
- Timeout 120s (vs 60s in skill_feedback)

### Confidence-Based Action

| Confidence | Action |
|-----------|--------|
| &ge; 7 | Auto-apply: modify SKILL.md / create new skill, register in DB, write `evolution_log` status=active, bump shared-skills generation |
| 4-6 | Propose: write `evolution_log` status=proposed, admin reviews in dashboard |
| < 4 | Discard |

No keyword matching. No five-tier policy. Haiku makes the call.

When modifying an existing skill (confidence &ge; 7), delegate to
`DBSkillFeedbackManager.create_version()` for proper backup + version tracking
instead of manual file rename. This reuses the existing `skill_versions` table.

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
    proposed_content TEXT,                            -- suggested SKILL.md content when status=proposed (admin reviews this)
    baseline_composite REAL,                          -- composite score at evolution time (7-day pre-evolution baseline)
    status TEXT NOT NULL DEFAULT 'active',           -- active / proposed / under_review / rolled_back
    created_at INTEGER NOT NULL,
    reviewed_at INTEGER,
    reviewed_by TEXT,
    review_decision TEXT,                            -- kept / rolled_back
    auto_rollback_at INTEGER                         -- 48h after under_review
);

CREATE INDEX idx_evolution_log_status ON evolution_log(status);
CREATE INDEX idx_evolution_log_skill ON evolution_log(skill_name);

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
session_learner
    │
    ├── confidence >= 7
    │       │
    │       ▼
    │  ┌─────────┐
    ├─▶│ active  │◀─── kept (admin)
    │  └────┬────┘
    │       │ 7 consecutive days composite < baseline_composite
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

    confidence 4-6
        │
        ▼
   ┌──────────┐     admin approve
   │ proposed │──────────────────┐
   └────┬─────┘                  │
        │ admin discard          │
        ▼                        ▼
   (deleted)               ┌─────────┐
                           │ active  │
                           └─────────┘
```

### Rollback Implementation

`evolution_rollback.execute_rollback()` uses `SkillManager.rollback_version()` (from `src/skill_manager.py` line 382), **not** `DBSkillFeedbackManager.rollback_version()` (from `src/skill_feedback.py` line 339). Reason: `SkillManager` resolves skill paths from the DB and handles both shared and personal skills; `DBSkillFeedbackManager` requires a `skills_dir` parameter that isn't always available.

### Daily Evaluation Task

A simple daily task (02:00) in `collective_intelligence.py`:

```python
async def _eval_snapshot_loop():
    for log in get_active_evolutions():
        snap = compute_snapshot(log)
        save_snapshot(snap)
        last_7 = get_last_7_snapshots(log.id)
        # baseline_composite is stored at evolution creation time
        if len(last_7) >= 7 and all(s.composite < log.baseline_composite for s in last_7):
            mark_under_review(log)  # sets auto_rollback_at = now + 48h

    # Check expired under_review
    for log in get_expired_reviews():  # auto_rollback_at < now
        execute_rollback(log)
```

---

## 3. Admin APIs

All require `Depends(require_admin)`.

| Endpoint | Purpose |
|----------|---------|
| `GET /api/admin/evolution/overview` | List all evolutions, filter `?status=` (active/proposed/under_review/rolled_back), paginated |
| `GET /api/admin/evolution/{id}` | Detail: version info + snapshot trend + signal breakdown |
| `GET /api/admin/evolution/{id}/diff` | SKILL.md diff (from → to) |
| `POST /api/admin/evolution/{id}/review` | Admin decision: `{"decision": "keep" \| "rollback" \| "discard"}`. "discard" deletes proposed entries |

For `status=proposed` entries, the review endpoint accepts:
- `{"decision": "keep"}` → promotes to `active`, applies the proposed SKILL.md change
- `{"decision": "discard"}` → deletes the proposal (no file changes made)

---

## 4. Admin Dashboard

Route: `/dashboard/evolution`. Two views:

### Overview Table

Filterable by status (All / Active / Proposed / Under Review / Rolled Back). Columns: skill name, version, source, composite score, status badge, days active.

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

1. Session with skill usage ends → `session_learner` runs → check `evolution_log` for new entry (status=active if confidence &ge; 7)
2. Medium-confidence finding (4-6) → `evolution_log` status=proposed → appears in admin dashboard under "Proposed" filter
3. Admin approves proposal via `POST /review {"decision": "keep"}` → status transitions to `active`, SKILL.md applied
4. Admin discards proposal via `POST /review {"decision": "discard"}` → record deleted
5. New pattern from session → new `SKILL.md` + `skill-meta.json` in `data/shared-skills/{skill-name}/` + DB registered + gen bumped
6. Insert 7 consecutive snapshots with composite < baseline_composite → status transitions to `under_review`
7. `under_review` + 48h no admin action → auto-rollback executes via `SkillManager.rollback_version()`
8. Admin POST `{"decision": "keep"}` on under_review item → status returns to `active`
9. Navigate `/dashboard/evolution` → table renders with status filter, click row → detail with charts and diff
10. Long session (300+ messages) → truncated to last 200 messages, each capped at 2000 chars → Haiku still returns valid JSON

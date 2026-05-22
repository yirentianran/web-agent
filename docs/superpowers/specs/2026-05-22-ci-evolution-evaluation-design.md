# Collective Intelligence — Evolution Evaluation & Admin Dashboard

## Context

The web-agent Collective Intelligence system has four background loops (wiki mining, pattern extraction, auto-promotion, auto-evolution) with a five-tier AutoEvolvePolicy (APPLY_EDITS → AUTO_FIX → PROPOSE → REQUIRE_REVIEW → SKIP). However, there is **no post-evolution verification** — the system auto-evolves skills but never checks whether the evolution actually improved or degraded skill quality. Evolution results are only passively visible through the next cycle's feedback aggregation, which may take weeks.

This spec adds: (1) an ECC-inspired session-based evolution engine that uses full conversation context instead of keyword matching, (2) an automated evaluation/verification system, (3) semi-automatic rollback for degraded evolutions, and (4) an admin dashboard for monitoring the CI evolution lifecycle.

## Scope

- **Backend**: 4 new modules (`session_learner.py`, `evolution_log.py`, `evolution_evaluator.py`, `evolution_rollback.py`), 5 new admin APIs, evolution pipeline rework
- **Frontend**: 1 new page at `/dashboard/evolution`, 6 new components, Monaco DiffEditor dependency
- **Database**: 2 new tables (`evolution_log`, `skill_eval_snapshots`); existing `messages` table becomes evolution data source
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

## 2. Evolution Engine — Dual-Source Design

Web-agent's evolution currently has ONE data source (`skill_feedback` table — user ratings and short comments). It uses keyword matching (`_summarize_feedback`) to determine HOW to optimize, then calls Haiku with thin context ("bug: timeout, error — fix this skill").

Inspired by Everything Claude Code's `continuous-learning` skill, we add a SECOND data source that uses **full conversation context from the `messages` table**.

### Comparison: Old vs New vs ECC

| | Old Web Agent | **New Web Agent** | ECC |
|---|---|---|---|
| Data source | `skill_feedback` (ratings + short comments) | `skill_feedback` + **`messages` table (full conversation)** | Session transcript file |
| Analysis | Keyword matching (`BUG_KEYWORDS`, `VAGUE_KEYWORDS`) | **LLM reads full context, identifies patterns** | Stop hook Claude reasoning |
| How to optimize | Haiku blind-fixes from bug keywords | **Haiku sees actual errors and conversation flow** | Claude extracts from transcript |
| Trigger | 4h background loop | 4h loop + **session end** | Stop hook |
| Output | Modified SKILL.md (vertical only) | Modified SKILL.md + **new learned skills** (vertical + horizontal) | New learned skill files (horizontal only) |
| Evaluation | None | evolution_log + composite scoring + rollback | EDD eval harness (manual) |

### Dual Data Sources

#### Source 1: Feedback-based (existing, retained)

`skill_feedback` table — user ratings, comments, and edits. This remains as the quick-reaction path for user-initiated changes (APPLY_EDITS) and explicit complaints.

#### Source 2: Session-based (NEW, ECC-inspired)

`messages` table JOIN `skill_usage` — captures the full conversation context around skill invocations. This replaces keyword matching as the primary "how to optimize" mechanism.

```
Session ends
    ↓
Query messages table for the session:
  SELECT m.seq, m.type, m.name, m.content
  FROM messages m
  WHERE m.session_id = ?
  ORDER BY m.seq
    ↓
JOIN skill_usage to identify which skills were active and when:
  SELECT su.skill_name, su.invoked_at
  FROM skill_usage su
  WHERE su.session_id = ?
    ↓
Assemble context for Haiku:
  - Full conversation messages (already structured: type/name/content)
  - Which skills were called at which points
  - Tool call outputs and error messages
  - User feedback submitted during session
    ↓
Haiku analysis prompt:
  "Analyze this session and identify:
   1. Existing skills that performed poorly — specific issues, context, suggested fixes
   2. Reusable patterns the user employed — can this become a new skill?
   Output as JSON."
    ↓
Output:
  → improvements → write to skill_feedback (source='ai_session_analysis')
  → new_patterns → create SKILL.md in data/shared-skills/learned/
```

### New Modules

| Module | Responsibility | Called by |
|--------|---------------|-----------|
| `src/session_learner.py` | Query `messages` + `skill_usage`, assemble Haiku analysis prompt, process improvements and new patterns | Session-end callback, CI scheduler |
| `src/evolution_log.py` | CRUD for `evolution_log` + `skill_eval_snapshots` | evaluator, rollback, APIs |
| `src/evolution_evaluator.py` | Daily snapshot generation, composite scoring, degradation detection | CI scheduler, APIs |
| `src/evolution_rollback.py` | Rollback state machine, 48h auto-rollback timer | evaluator, APIs |

### Modified Modules

**`main_server.py`** — Session-end callback:
- When a session completes (WebSocket close / session cleanup), call `session_learner.analyze_session(session_id)`
- Fire-and-forget background task; does not block session cleanup

**`src/collective_intelligence.py`** — Rework `_auto_evolve_loop`:
1. `_auto_evolve_loop()`: after successful evolution, write to `evolution_log` (status=active)
2. New `_eval_snapshot_loop()`: daily at 02:00 — snapshot all active/under_review records
3. `_auto_evolve_loop` now processes BOTH feedback-based candidates AND session-based findings

**`src/auto_evolve.py`** — Supplemented (not replaced):
- `_summarize_feedback()` keyword matching retained as fallback for feedback-based candidates
- New `analyze_session_findings()` processes Haiku-generated analysis results
- Policy priority: APPLY_EDITS → AUTO_FIX (LLM-confident) → PROPOSE → REQUIRE_REVIEW → SKIP
- Cooldown period: 7 days (checked against `evolution_log`)
- AUTO_FIX confidence gate: Haiku self-scores its fix (1-10), <6 demoted to PROPOSE
- HIGH_USAGE_THRESHOLD: 100

### Evolution Comparison Summary

```
┌── Feedback-based (existing) ──────────────────┐
│  skill_feedback table                          │
│       ↓                                        │
│  Keyword matching (_summarize_feedback)        │
│       ↓                                        │
│  APPLY_EDITS / AUTO_FIX (Haiku blind-fix)      │
│       ↓                                        │
│  Modified SKILL.md                             │
└────────────────────────────────────────────────┘

┌── Session-based (NEW, ECC-inspired) ──────────┐
│  messages table + skill_usage (DB)             │
│       ↓                                        │
│  Haiku analyzes full conversation context      │
│       ↓                                        │
│  Identifies:                                   │
│    - Existing skill improvements (→ feedback)  │
│    - New reusable patterns (→ new skills)      │
│       ↓                                        │
│  Modified SKILL.md + new learned skills         │
└────────────────────────────────────────────────┘

┌── Evaluation (NEW) ────────────────────────────┐
│  evolution_log + skill_eval_snapshots          │
│       ↓                                        │
│  Composite scoring (rating + usage + success)  │
│       ↓                                        │
│  Degradation → under_review → 48h rollback    │
└────────────────────────────────────────────────┘
```

### State Machine

```
    auto_evolve / session_learner
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

## 4. Session Learner — ECC-Inspired Evolution

### Haiku Analysis Prompt

The `session_learner` sends the following structured context to Haiku:

**Input:**
- Full `messages` rows for the session (seq, type, name, content)
- `skill_usage` data showing which skills were called and when
- Existing `skill_feedback` for those skills (if any)
- Current SKILL.md for each skill used in the session

**Prompt template:**
```
You are analyzing a completed AI agent session to improve skills.

## Session Messages
{messages formatted as: [seq] type:name → content}

## Skills Used in This Session
{skill_name}: called at {timestamp}, current SKILL.md: {content}

## Existing User Feedback for These Skills
{ratings and comments from skill_feedback}

## Analysis Tasks

1. **Skill improvements**: For each skill used, did it perform well?
   - What went wrong? (look for errors, user corrections, retries)
   - What should change in SKILL.md to prevent this?
   - If no issues, say so.

2. **New patterns**: Did the user demonstrate any reusable workflow?
   - A troubleshooting technique
   - A specific tool usage pattern
   - A workaround for a limitation
   → Could this become a new reusable skill?

Output as JSON:
{
  "improvements": [
    {
      "skill_name": "xxx",
      "confidence": 1-10,
      "issue": "specific issue description with context",
      "suggested_fix": "updated SKILL.md content or specific changes"
    }
  ],
  "new_patterns": [
    {
      "name": "descriptive-kebab-case-name",
      "description": "what this pattern does and when to use it",
      "suggested_skill_content": "full SKILL.md if confident, or outline"
    }
  ]
}
```

### Output Handling

**For improvements** (confidence >= 6):
- Write to `skill_feedback` table with `source='ai_session_analysis'` and `skill_version` tracking
- Feed into existing `auto_evolve` pipeline as AUTO_FIX candidates
- The Haiku-generated `suggested_fix` replaces the blind-fix approach

**For improvements** (confidence < 6):
- Write as PROPOSE candidate for admin review

**For new patterns** (confidence >= 7):
- Create `data/shared-skills/learned/{name}/SKILL.md`
- Register in `skills` table with `source='learned'`
- Create `evolution_log` entry with `evolve_policy='SESSION_LEARNED'`

**For new patterns** (confidence < 7):
- Save as draft for admin review in the dashboard

### Implementation

```python
# src/session_learner.py

class SessionLearner:
    def __init__(self, db: Database, skills_dir: Path):
        self.db = db
        self.skills_dir = skills_dir

    async def analyze_session(self, session_id: str) -> dict:
        """Analyze a completed session and extract learnings."""
        # 1. Query messages + skill_usage from DB
        messages = await self._get_session_messages(session_id)
        skills_used = await self._get_session_skills(session_id)

        if not skills_used:
            return {"improvements": [], "new_patterns": []}

        # 2. Build analysis context
        context = self._build_analysis_context(messages, skills_used)

        # 3. Call Haiku for analysis
        result = await self._call_haiku_analyze(context)

        # 4. Process improvements → skill_feedback table
        for imp in result.get("improvements", []):
            await self._process_improvement(imp, session_id)

        # 5. Process new patterns → create learned skills
        for pat in result.get("new_patterns", []):
            await self._process_new_pattern(pat, session_id)

        return result
```

### Comparison with Old AUTO_FIX

| | Old AUTO_FIX | New Session Learner |
|---|---|---|
| Data | Bug keywords from comments | Full conversation messages |
| Context | "timeout, error" | Actual error messages, user corrections, tool outputs |
| Skill content | Current SKILL.md only | SKILL.md + conversation context + existing feedback |
| Output | Modified SKILL.md | Modified SKILL.md + new learned skills |
| Quality | Blind guess from keywords | Informed analysis from real behavior |

---

## 5. Testing Strategy

### Backend Tests (`tests/unit/test_session_learner.py`)

- `_get_session_messages()` returns messages ordered by seq
- `_get_session_skills()` returns skills used in the session
- `_build_analysis_context()` formats messages and skills into correct prompt structure
- `_call_haiku_analyze()` handles success response (JSON parsed correctly)
- `_call_haiku_analyze()` handles error response (returns empty improvements/patterns)
- `_process_improvement()` writes to `skill_feedback` table with correct source flag
- `_process_new_pattern()` creates SKILL.md in correct directory
- Session with no skills used → early return empty result

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
2. **Session learner**: Complete a session that invokes skills → `session_learner.analyze_session()` runs → check `skill_feedback` for AI-generated entries with `source='ai_session_analysis'`
3. **New pattern creation**: Session with a novel workflow → Haiku identifies pattern → `data/shared-skills/learned/{name}/SKILL.md` created
4. **Policy cooldown**: Trigger auto-evolve twice for the same skill within 7 days → second attempt SKIPs
5. **Snapshot generation**: Run `_eval_snapshot_loop()` → verify snapshots created for all active evolutions
6. **Degradation → under_review**: Manually insert 7 low-score snapshots → next loop run transitions to `under_review`
7. **Auto-rollback**: Set `auto_rollback_at` in the past → next loop run executes rollback
8. **API integration**: `curl /api/admin/evolution/overview` with admin token returns paginated list (includes both feedback-based and session-learned evolutions)
9. **UI overview**: Navigate to `/dashboard/evolution`, verify table renders with mixed evolution sources
10. **UI detail**: Click into a session-learned evolution → detail page shows Haiku-identified issue context alongside score trend
11. **Diff**: Click into any evolution → Monaco DiffEditor shows side-by-side markdown diff
12. **Admin review**: POST `{"decision": "keep"}` → status transitions back to `active`

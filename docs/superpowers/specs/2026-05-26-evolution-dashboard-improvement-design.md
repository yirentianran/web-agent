# Evolution Dashboard Improvement Design

## Context

The evolution monitoring dashboard (`/evolution` route) provides a web UI for observing the automated skill evolution pipeline (observations → instincts → evolutions). Analysis identified six issues across data correctness, missing information, and interaction capability.

## Scope

Improve the dashboard without changing its overall architecture. Three tabs (Evolutions / Instincts / Observations) and the detail panel remain. Six targeted improvements:

### 1. Signal Baseline — Real Pre-Evolution Snapshots

**Problem:** `SignalBreakdown` compares current metrics against hardcoded constants (`DEFAULT_BASELINE_RATING = 4.0`, `DEFAULT_BASELINE_DAILY_USAGE = 10`, `DEFAULT_BASELINE_SUCCESS_RATE = 0.80`), making delta percentages meaningless.

**Fix:** When an evolution is created (auto-applied or proposed), capture a pre-evolution metrics snapshot from the 7 days of observation data before the evolution's `created_at`. Store it as `baseline_metrics` JSON on `evolution_log`. The signal breakdown endpoint reads this stored baseline instead of the constants.

**Schema change:** Add `baseline_metrics TEXT` column to `evolution_log`.

### 2. Pipeline Funnel — Consistent Time Window

**Problem:** Funnel mixes `today_events` (today only) with all-time instinct/evolution counts.

**Fix:** All four funnel stages use the same time window (controlled by the time range selector). The `/api/admin/evolution/stats` endpoint accepts a `days` parameter.

### 3. Observation Browser — Show Tool Input/Output

**Problem:** `tool_input_summary` and `tool_output_summary` exist in the database but are not returned by the list API or displayed in the frontend.

**Fix:** Return them from `GET /api/admin/observations` and add columns to the ObservationBrowser table.

### 4. Time Range Selector

**Problem:** No time dimension control. Can't view trends for different periods.

**Fix:** Add a segmented button bar (Today / Last 7 days / Last 30 days / All) at the top of the dashboard. Selection affects StatsCards, PipelineFunnel, and OverviewTable via the `days` query parameter.

### 5. Evolution Table — Richer Columns

**Problem:** OverviewTable shows only skill, version, source, status, created. Fields like instinct count and composite score exist in the data but aren't shown.

**Fix:** Add three columns: instinct count, composite score (with color: green ≥ 0.7, yellow ≥ 0.5, red < 0.5), and days active.

### 6. Auto-Refresh

**Problem:** Data is a static snapshot from page load.

**Fix:** Poll stats/overview every 30 seconds via `setInterval` in EvolutionPage. Show refresh indicator. Pause polling when viewing detail.

## Files to Modify

| File | Changes |
|------|---------|
| `src/database.py` | Add `baseline_metrics TEXT` to `evolution_log` table (migration) |
| `src/evolution_log.py` | Store baseline on creation; accept `days` param in `get_overview_stats`; return baseline in detail |
| `src/instinct_extractor.py` | Capture pre-evolution metrics when creating evolution records |
| `src/evolution_signals.py` | Use stored baseline instead of hardcoded constants |
| `main_server.py` | Update stats endpoint with `days` param; include `tool_input_summary`/`tool_output_summary` in observations list; return baseline metrics in detail endpoint |
| `src/observation.py` | Add `tool_input_summary`/`tool_output_summary` to `list_events` response |
| `frontend/src/pages/EvolutionPage.tsx` | Time range selector state + auto-refresh interval |
| `frontend/src/pages/evolution/StatsCards.tsx` | Accept time range prop, show delta vs previous period |
| `frontend/src/pages/evolution/PipelineFunnel.tsx` | Show consistent time window label |
| `frontend/src/pages/evolution/OverviewTable.tsx` | Add instinct_count, composite_score, days_active columns |
| `frontend/src/pages/evolution/EvolutionDetail.tsx` | Show real baseline in signal breakdown |
| `frontend/src/pages/evolution/SignalBreakdown.tsx` | Display baseline source info |
| `frontend/src/pages/evolution/ObservationBrowser.tsx` | Add input_summary and output_summary columns |
| `frontend/src/hooks/useEvolutionApi.ts` | Add `days` param to fetchStats, add fields to ObservationItem type |
| `frontend/src/pages/evolution/evolution.css` | Style new elements |

## Verification

1. Run backend tests: `uv run pytest tests/unit/test_evolution_api.py tests/unit/test_evolution_signals.py tests/unit/test_instinct_extractor.py tests/unit/test_observation.py`
2. Run frontend type check: `cd frontend && npx tsc --noEmit`
3. Start dev server, open `/evolution`, verify:
   - Time range buttons switch data correctly
   - Funnel numbers all reference the same time window label
   - Evolution table shows new columns with real values
   - Click into an evolution detail → signal breakdown shows "baseline: pre-evolution 7-day avg"
   - Observations tab shows input_summary and output_summary
   - Page auto-refreshes (check network tab for 30s polling)

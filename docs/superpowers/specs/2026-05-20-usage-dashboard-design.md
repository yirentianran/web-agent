# Usage Dashboard — Design Spec

## Context

The web agent backend already captures rich data (session tokens, message tokens, skill usage, container resources), but the frontend has no usage monitoring page. Administrators have no way to see system activity without querying the database directly. This feature adds an admin-only dashboard with overview metrics, trends, rankings, and resource monitoring.

## Scope

- Admin-only, independent page at `/dashboard`
- Time filtering: Today, 7 Days, 30 Days, custom date range
- Backend: 3 new aggregation APIs + 1 data fix (store model name in usage JSON)
- Frontend: 1 new page, 7 new components, 1 new hook, 1 new dependency (Recharts)

## Prerequisite Data Fix

### Store model name in usage JSON

**File:** `main_server.py` — `message_to_dicts()`

Add `model` field to the stored `usage` JSON. The model name comes from the agent options configuration.

Stored `messages.usage` after fix:

```json
{
  "input_tokens": 1000,
  "output_tokens": 500,
  "cache_read_tokens": 200,
  "cache_write_tokens": 100,
  "model": "claude-opus-4-6"
}
```

## Backend APIs

All three endpoints use `Depends(require_admin)`. Time params validated: `from` <= `to`, range <= 365 days, default `from` = 30 days ago.

### `GET /api/admin/dashboard/overview`

Query params: `from`, `to`

```json
{
  "active_users": 18,
  "total_users": 42,
  "new_users": 3,
  "total_sessions": 256,
  "total_input_tokens": 3200000,
  "total_output_tokens": 1800000,
  "total_cache_read_tokens": 1500000,
  "total_cache_write_tokens": 280000
}
```

SQL sources:
- `active_users`: `SELECT COUNT(DISTINCT user_id) FROM sessions WHERE last_active_at BETWEEN :from AND :to`
- `total_users`: `SELECT COUNT(*) FROM users WHERE created_at <= :to`
- `new_users`: `SELECT COUNT(*) FROM users WHERE created_at BETWEEN :from AND :to`
- `total_sessions`: `SELECT COUNT(*) FROM sessions WHERE created_at BETWEEN :from AND :to`
- Token fields: `SELECT SUM(json_extract(m.usage, '$.input_tokens')) ... FROM messages m JOIN sessions s ON m.session_id = s.session_id WHERE s.created_at BETWEEN :from AND :to`

### `GET /api/admin/dashboard/trends`

Query params: `from`, `to`

```json
{
  "daily_active_users": [{"date": "2026-05-01", "count": 12}],
  "daily_sessions": [{"date": "2026-05-01", "count": 18}],
  "daily_tokens": [
    {
      "date": "2026-05-01",
      "input": 50000,
      "output": 20000,
      "cache_read": 18000,
      "cache_write": 3000
    }
  ]
}
```

Daily active users: `SELECT date(last_active_at) as date, COUNT(DISTINCT user_id) FROM sessions WHERE last_active_at BETWEEN :from AND :to GROUP BY date(last_active_at)`

Daily sessions: `SELECT date(created_at) as date, COUNT(*) FROM sessions WHERE created_at BETWEEN :from AND :to GROUP BY date(created_at)`

Daily tokens: join messages with sessions, group by `date(m.created_at)`.

### `GET /api/admin/dashboard/rankings`

Query params: `from`, `to`

```json
{
  "top_users": [
    {
      "user_id": "u1",
      "total_tokens": 520000,
      "session_count": 32
    }
  ],
  "top_skills": [
    {
      "skill_name": "code-reviewer",
      "use_count": 56,
      "unique_users": 12
    }
  ]
}
```

Top users: aggregate `messages.usage` JOIN `sessions` on session_id, GROUP BY `sessions.user_id`, order by total tokens DESC, LIMIT 10.

Top skills: extend `SkillManager.get_top_skills()` with time filtering, join `skill_usage` with `sessions` to filter by session creation date.

## Frontend

### Route

`/dashboard` — new route in `App.tsx`, wrapped with role check (admin only).

SettingsMenu entry: "Usage Dashboard" — navigates to `/dashboard`, visible only when `userRole === "admin"`.

### Component Tree

```
DashboardPage
├── TimeRangeSelector     — preset buttons (Today/7d/30d) + custom date picker
├── OverviewCards          — 5 stat cards with period-over-period deltas
├── TokenTrendChart        — 4-line chart (Input/Output/Cache Read/Cache Write)
├── ActivityTrendChart     — 2-line chart (DAU / sessions)
├── UserRankingTable       — top 10 users by token consumption
├── SkillRankingTable      — top 10 skills by invocation count
└── ResourcePanel          — summary row + per-container detail table
```

### Page Layout

```
┌──────────────────────────────────────────────────────────┐
│  ← Back    Usage Dashboard     [Today | 7 Days | 30 Days | 📅] │
├──────────────────────────────────────────────────────────┤
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ │
│  │ Active │ │ Total  │ │  New   │ │  Total │ │ Token  │ │
│  │ Users  │ │ Users  │ │ Users  │ │Sessions│ │  Usage │ │
│  │  18    │ │  42    │ │  +3    │ │  256   │ │6.78M   │ │
│  │ ↑12%  │ │        │ │ ↑50%  │ │ ↑8%   │ │I 3.2M  │ │
│  │        │ │        │ │        │ │        │ │O 1.8M  │ │
│  └────────┘ └────────┘ └────────┘ └────────┘ └────────┘ │
│                                                           │
│  Token Trend (4-line: Input / Output / Cache R / Cache W) │
│                                                           │
│  DAU & Session Trend (2-line)                             │
│                                                           │
│  ┌── User Token Top 10 ───┐ ┌── Skill Usage Top 10 ───┐  │
│  │ #  User   Tokens  Sess │ │ #  Skill       Uses  Usr │  │
│  │ 1  u1     520K    32   │ │ 1  code-review  56   12 │  │
│  │ 2  u2     380K    25   │ │ 2  tdd-guide    42    8 │  │
│  └────────────────────────┘ └─────────────────────────┘  │
│                                                           │
│  ┌── Container Resources ────────────────────────────┐   │
│  │  ● Running: 8   CPU: 34%   Mem: 3.2/16GB          │   │
│  │  ┌─────────────────────────────────────────────┐  │   │
│  │  │ User  Container       CPU   Mem    Disk  St │  │   │
│  │  │ u1    web-agent-u1   5.2%  420MB  2.8GB  ●  │  │   │
│  │  │ u2    web-agent-u2  12.1%  1.1GB  5.2GB  ●  │  │   │
│  │  │ u5    web-agent-u5  89.7%  3.8GB 12.1GB  ⚠  │  │   │
│  │  └─────────────────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

### Data Hook

```ts
// frontend/src/hooks/useDashboardApi.ts
useDashboardApi() → {
  overview: { data, loading, error },
  trends: { data, loading, error },
  rankings: { data, loading, error },
  refetch(from, to)  // called when time range changes
}
```

Three parallel fetch calls on mount and time range change. Each independently handles loading/error states so one failure doesn't block other sections.

### Charting

Recharts, dynamically imported in DashboardPage:

```ts
const { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } =
  await import('recharts');
```

Not bundled into the main chat page.

### Time Range Logic

- Preset buttons: Today, 7 Days, 30 Days — clicking sets `from`/`to` immediately
- Custom: opens a date range picker (two `<input type="date">` or a lightweight calendar)
- Period-over-period comparison: if selected 7 days, compare vs previous 7 days; if 30 days, compare vs previous 30 days

## Implementation Order

1. **Data fix** — store model name in `messages.usage`
2. **Backend APIs** — overview, trends, rankings endpoints
3. **Frontend hook + page** — useDashboardApi, DashboardPage, routing
4. **Components** — TimeRangeSelector, OverviewCards, charts, tables, ResourcePanel
5. **Navigation** — SettingsMenu entry

## Verification

- Fix verification: check `messages.usage` JSON in SQLite contains `model` field after a session completes
- API verification: `curl /api/admin/dashboard/overview?from=2026-05-01&to=2026-05-20` with admin token returns expected aggregates
- UI verification: navigate to `/dashboard`, switch time presets, verify charts render, verify tables sort correctly, verify container table shows running containers
- Edge cases: empty data (no sessions in range), single-user system, custom range with from > to (should show validation error), non-admin user sees no dashboard link and gets 403 on API

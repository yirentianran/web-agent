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

Date parsing: uses `datetime.fromisoformat()` which accepts both `YYYY-MM-DD` (date-only, back-compat with presets) and `YYYY-MM-DDTHH:MM` (datetime, from custom picker). When the string contains no `T`, day-boundary logic applies (`from` at 00:00:00, `to` at 23:59:59 in `PROJECT_TZ`). When `T` is present, the exact datetime is used. All timestamps in `PROJECT_TZ` (UTC+8).

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

Query params: `from`, `to`, `interval` (default `day`)

| Interval | SQL group expression | Use case |
|---|---|---|
| `5min` | `strftime('%Y-%m-%dT%H:%M', timestamp)` | Today view (288 data points) |
| `hour` | `strftime('%Y-%m-%dT%H:00', timestamp)` | 1-3 day range |
| `day` | `date(timestamp)` | 7d / 30d / custom long range |

When `interval` is `5min` or `hour`, the chart X-axis shows time labels (`HH:MM`). The backend returns the `date` field (actual key used in API responses) with full timestamp values for sub-day intervals.

```json
// interval=day
{
  "interval": "day",
  "daily_active_users": [{"date": "2026-05-01", "count": 12}],
  "daily_sessions": [{"date": "2026-05-01", "count": 18}],
  "daily_tokens": [
    {"date": "2026-05-01", "input": 50000, "output": 20000, "cache_read": 18000, "cache_write": 3000}
  ]
}

// interval=5min
{
  "interval": "5min",
  "active_users": [{"datetime": "2026-05-20T14:30", "count": 5}],
  "sessions": [{"datetime": "2026-05-20T14:30", "count": 3}],
  "tokens": [
    {"datetime": "2026-05-20T14:30", "input": 1200, "output": 800, "cache_read": 500, "cache_write": 100}
  ]
}
```

SQL:
- `interval` determines the `GROUP BY` expression via a lookup dict (never string interpolation)
- Active users 5min: `SELECT strftime('%Y-%m-%dT%H:%M', last_active_at) as bucket, COUNT(DISTINCT user_id) FROM sessions WHERE last_active_at BETWEEN :from AND :to GROUP BY bucket`
- Sessions 5min: `SELECT strftime('%Y-%m-%dT%H:%M', created_at) as bucket, COUNT(*) FROM sessions WHERE created_at BETWEEN :from AND :to GROUP BY bucket`
- Tokens 5min: join messages with sessions, group by `strftime('%Y-%m-%dT%H:%M', m.created_at)`

Frontend chart X-axis: interval `5min` shows time labels (`HH:MM`), interval `day` shows date labels (`MM-DD`).

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
├── TimeRangeSelector     — preset buttons (Today/7d/30d) + custom datetime-local picker
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

### Date Utilities

`frontend/src/lib/dates.ts` 提供了时间格式化辅助函数：

```ts
formatDate(d: Date) → 'YYYY-MM-DD'           // 纯日期格式，用于预设按钮
formatDatetime(d: Date) → 'YYYY-MM-DDTHH:MM'  // 日期时间格式，用于 datetime-local 输入
todayStr() → 'YYYY-MM-DD'                     // 今日日期
nowStr() → 'YYYY-MM-DDTHH:MM'                 // 当前日期时间
daysAgoStr(n: number) → 'YYYY-MM-DD'          // n 天前的日期
```

`TimeRangeSelector.tsx` 中也定义了一个本地 `formatDatetime()` 副本，避免跨组件耦合。

### Data Hook

```ts
// frontend/src/hooks/useDashboardApi.ts
useDashboardApi() → {
  overview: { data, loading, error },
  trends: { data, loading, error },
  rankings: { data, loading, error },
  refetch(from, to, interval?)  // interval: '5min' | 'hour' | 'day'
}
```

Three parallel fetch calls on mount and time range change. Each independently handles loading/error states so one failure doesn't block other sections.

`refetch` auto-selects interval when omitted: `5min` for Today, `hour` for ranges ≤ 3 days, `day` for 7d/30d/custom.

### Charting

Recharts, dynamically imported in DashboardPage:

```ts
const { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } =
  await import('recharts');
```

Not bundled into the main chat page.

### Time Range Logic

- Preset buttons: Today, 7 Days, 30 Days — clicking sets `from`/`to` immediately (date-only `YYYY-MM-DD` strings)
- Custom: expands an inline `datetime-local` range picker (two `<input type="datetime-local">`), allows selecting time-of-day down to minutes (e.g. `2026-05-20T10:00` ~ `2026-05-20T14:00`)
  - When the custom panel opens, existing range values are automatically converted to `datetime-local` format via `formatDatetime()`
  - Date-only strings (from presets) receive `T00:00` / `T23:59` suffixes for backward compatibility
- Period-over-period comparison: if selected 7 days, compare vs previous 7 days; if 30 days, compare vs previous 30 days
- Interval auto-selection: Today → `5min`, ≤3 days → `hour`, \>3 days → `day`
- The `interval` parameter is sent to the trends API; overview and rankings are unaffected (they are totals for the range)

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
- Custom datetime range: select `2026-05-20T10:00` ~ `2026-05-20T14:00`, verify interval is `5min`, verify charts show time labels (`HH:MM`) on X-axis, verify overview filters to that 4-hour window
- Edge cases: empty data (no sessions in range), single-user system, custom range with from > to (should show validation error), non-admin user sees no dashboard link and gets 403 on API
- Backend datetime parsing: date-only strings (`YYYY-MM-DD`) still parsed as day boundaries; strings with `T` parsed as exact timestamps in PROJECT_TZ

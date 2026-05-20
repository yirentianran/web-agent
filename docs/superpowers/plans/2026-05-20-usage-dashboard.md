# Usage Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an admin-only usage dashboard at `/dashboard` with overview metrics, trend charts, user/skill rankings, and container resource monitoring.

**Architecture:** Three new backend aggregation endpoints query existing SQLite tables (sessions, messages, skill_usage) with time-range filtering. A new frontend page at `/dashboard` fetches data via a custom hook and renders it with Recharts (dynamic import) for trend charts and plain CSS for stat cards and ranking tables.

**Tech Stack:** FastAPI + aiosqlite (backend), React + Recharts + CSS (frontend)

---

## File Map

| Action | File | Purpose |
|--------|------|---------|
| Modify | `main_server.py:2214` | Pass model to `message_to_dicts` |
| Modify | `main_server.py:1585` | Accept optional `model` param, inject into usage |
| Modify | `main_server.py` (new endpoints) | Three dashboard aggregation APIs |
| Create | `tests/unit/test_dashboard_api.py` | Backend tests for dashboard endpoints |
| Create | `frontend/src/hooks/useDashboardApi.ts` | Data fetching hook |
| Create | `frontend/src/components/dashboard/TimeRangeSelector.tsx` | Time range preset buttons + date picker |
| Create | `frontend/src/components/dashboard/TimeRangeSelector.css` | Styles for time range selector |
| Create | `frontend/src/components/dashboard/OverviewCards.tsx` | Five stat cards |
| Create | `frontend/src/components/dashboard/OverviewCards.css` | Styles for stat cards |
| Create | `frontend/src/components/dashboard/TokenTrendChart.tsx` | 4-line token trend chart |
| Create | `frontend/src/components/dashboard/ActivityTrendChart.tsx` | 2-line DAU/sessions chart |
| Create | `frontend/src/components/dashboard/UserRankingTable.tsx` | Top 10 users table |
| Create | `frontend/src/components/dashboard/SkillRankingTable.tsx` | Top 10 skills table |
| Create | `frontend/src/components/dashboard/ResourcePanel.tsx` | Container resource panel |
| Create | `frontend/src/components/dashboard/ResourcePanel.css` | Styles for resource panel |
| Create | `frontend/src/components/dashboard/dashboard.css` | Shared dashboard styles |
| Create | `frontend/src/components/DashboardPage.tsx` | Page container |
| Modify | `frontend/src/App.tsx:30,1547,1672` | Add /dashboard route |
| Modify | `frontend/src/components/SettingsMenu.tsx:12,49` | Add menu entry |
| Install | `frontend/` | `recharts` npm package |

---

### Task 1: Store model name in `messages.usage`

**Files:**
- Modify: `main_server.py:1585-1633,2214`
- Test: `tests/unit/test_dashboard_api.py` (reuse in Task 2)

- [ ] **Step 1: Add optional `model` parameter to `message_to_dicts`**

In `main_server.py` line 1585, change the function signature:

```python
def message_to_dicts(msg: Any, model: str | None = None) -> Iterator[dict[str, Any]]:
```

- [ ] **Step 2: Inject model into usage dicts in `message_to_dicts`**

After line 1628 (`result["usage"] = msg.usage`), add model injection. Also handle `TaskNotificationMessage` (line 1643) and `TaskProgressMessage` (line 1653):

```python
# In ResultMessage block (after line 1628):
if msg.usage:
    result["usage"] = msg.usage
    if model:
        result["usage"]["model"] = model  # <-- add this line

# In TaskNotificationMessage block (after line 1643):
if msg.usage:
    result["cost_usd"] = msg.usage.get("total_cost_usd", 0)
    if model:
        msg.usage["model"] = model  # <-- add this line

# In TaskProgressMessage block (after line 1653):
if msg.usage:
    result["cost_usd"] = msg.usage.get("total_cost_usd", 0)
    if model:
        msg.usage["model"] = model  # <-- add this line
```

- [ ] **Step 3: Pass model from `run_agent_task` call site**

In `run_agent_task` (line 2214), pass `options.model`:

```python
for event in message_to_dicts(msg, model=options.model):
```

- [ ] **Step 4: Run existing tests to verify no regressions**

```bash
uv run pytest tests/unit/test_main_server.py -v
```

Expected: All existing tests pass.

- [ ] **Step 5: Commit**

```bash
git add main_server.py
git commit -m "fix: store model name in messages.usage JSON for dashboard aggregation"
```

---

### Task 2: Dashboard overview endpoint

**Files:**
- Modify: `main_server.py` (append new endpoint before `### Admin endpoints` section, around line 4697)
- Create: `tests/unit/test_dashboard_api.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_dashboard_api.py`:

```python
"""Tests for dashboard aggregation APIs."""
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client() -> TestClient:
    """Return a TestClient with admin auth bypass via ENFORCE_AUTH=false."""
    import os
    os.environ["ENFORCE_AUTH"] = "false"
    os.environ["JWT_SECRET"] = "test-secret"
    from main_server import app
    return TestClient(app)


class TestDashboardOverview:
    def test_overview_requires_admin_when_auth_enforced(self):
        """Return 403 when ENFORCE_AUTH=true and no valid admin token."""
        import os
        os.environ["ENFORCE_AUTH"] = "true"
        os.environ["JWT_SECRET"] = "test-secret"
        from main_server import app
        client = TestClient(app)
        resp = client.get("/api/admin/dashboard/overview?from=2026-01-01&to=2026-01-31")
        assert resp.status_code == 403
        os.environ["ENFORCE_AUTH"] = "false"

    def test_overview_returns_expected_structure(self, admin_client):
        """Overview endpoint returns correct JSON keys even with empty DB."""
        resp = admin_client.get(
            "/api/admin/dashboard/overview?from=2026-01-01&to=2026-01-31"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "active_users" in data
        assert "total_users" in data
        assert "new_users" in data
        assert "total_sessions" in data
        assert "total_input_tokens" in data
        assert "total_output_tokens" in data
        assert "total_cache_read_tokens" in data
        assert "total_cache_write_tokens" in data
        # Empty DB should return zeros
        assert data["active_users"] == 0

    def test_overview_validates_date_range(self, admin_client):
        """from > to should return 422."""
        resp = admin_client.get(
            "/api/admin/dashboard/overview?from=2026-12-31&to=2026-01-01"
        )
        assert resp.status_code == 422

    def test_overview_rejects_range_over_365_days(self, admin_client):
        """Range > 365 days should return 422."""
        resp = admin_client.get(
            "/api/admin/dashboard/overview?from=2025-01-01&to=2026-12-31"
        )
        assert resp.status_code == 422

    def test_overview_defaults_to_30_days(self, admin_client):
        """No params should default to last 30 days."""
        resp = admin_client.get("/api/admin/dashboard/overview")
        assert resp.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_dashboard_api.py::TestDashboardOverview -v
```

Expected: FAIL — 404 or 403 (endpoint not defined yet).

- [ ] **Step 3: Implement the overview endpoint**

Add in `main_server.py`, before the existing admin endpoints section (before line 4697):

```python
# ---- Dashboard APIs ----

@app.get("/api/admin/dashboard/overview")
async def dashboard_overview(
    from_date: str | None = None,
    to_date: str | None = None,
    current_user: str = Depends(require_admin),
) -> dict[str, Any]:
    """Aggregated usage overview for the dashboard.

    Query params:
        from_date: Start date YYYY-MM-DD (default: 30 days ago)
        to_date:   End date YYYY-MM-DD (default: today)
    """
    from datetime import date, timedelta

    # Default to last 30 days
    today = date.today()
    to_dt = date.fromisoformat(to_date) if to_date else today
    from_dt = date.fromisoformat(from_date) if from_date else today - timedelta(days=30)

    # Validate
    if from_dt > to_dt:
        raise HTTPException(status_code=422, detail="from_date must be <= to_date")
    if (to_dt - from_dt).days > 365:
        raise HTTPException(status_code=422, detail="Date range must not exceed 365 days")

    from_str = from_dt.isoformat()
    to_str = to_dt.isoformat()

    if _db is None:
        return {
            "active_users": 0, "total_users": 0, "new_users": 0,
            "total_sessions": 0, "total_input_tokens": 0,
            "total_output_tokens": 0, "total_cache_read_tokens": 0,
            "total_cache_write_tokens": 0,
        }

    async with _db.connection() as conn:
        # Active users (users with session activity in range)
        cursor = await conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM sessions "
            "WHERE last_active_at >= ? AND last_active_at <= ?",
            (from_str, to_str),
        )
        row = await cursor.fetchone()
        active_users = row[0] if row else 0

        # Total users
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at <= ?", (to_str,)
        )
        row = await cursor.fetchone()
        total_users = row[0] if row else 0

        # New users in range
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at >= ? AND created_at <= ?",
            (from_str, to_str),
        )
        row = await cursor.fetchone()
        new_users = row[0] if row else 0

        # Total sessions in range
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE created_at >= ? AND created_at <= ?",
            (from_str, to_str),
        )
        row = await cursor.fetchone()
        total_sessions = row[0] if row else 0

        # Token aggregation — join messages with sessions to filter by session date
        cursor = await conn.execute(
            "SELECT "
            "COALESCE(SUM(CAST(json_extract(m.usage, '$.input_tokens') AS INTEGER)), 0), "
            "COALESCE(SUM(CAST(json_extract(m.usage, '$.output_tokens') AS INTEGER)), 0), "
            "COALESCE(SUM(CAST(json_extract(m.usage, '$.cache_read_tokens') AS INTEGER)), 0), "
            "COALESCE(SUM(CAST(json_extract(m.usage, '$.cache_write_tokens') AS INTEGER)), 0) "
            "FROM messages m "
            "JOIN sessions s ON m.session_id = s.session_id "
            "WHERE m.created_at >= ? AND m.created_at <= ?",
            (from_str, to_str),
        )
        row = await cursor.fetchone()

    return {
        "active_users": active_users,
        "total_users": total_users,
        "new_users": new_users,
        "total_sessions": total_sessions,
        "total_input_tokens": row[0] if row else 0,
        "total_output_tokens": row[1] if row else 0,
        "total_cache_read_tokens": row[2] if row else 0,
        "total_cache_write_tokens": row[3] if row else 0,
    }
```

Note: This uses `_db` (the global Database instance from startup). Ensure `HTTPException` is imported — check line ~13 of main_server.py for the existing import.

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_dashboard_api.py::TestDashboardOverview -v
```

Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add main_server.py tests/unit/test_dashboard_api.py
git commit -m "feat: add dashboard overview API endpoint"
```

---

### Task 3: Dashboard trends endpoint

**Files:**
- Modify: `main_server.py` (append after overview endpoint)
- Modify: `tests/unit/test_dashboard_api.py`

- [ ] **Step 1: Write failing test**

Append to `tests/unit/test_dashboard_api.py`:

```python
class TestDashboardTrends:
    def test_trends_returns_expected_structure(self, admin_client):
        """Trends endpoint returns correct JSON structure."""
        resp = admin_client.get(
            "/api/admin/dashboard/trends?from=2026-01-01&to=2026-01-31"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "daily_active_users" in data
        assert "daily_sessions" in data
        assert "daily_tokens" in data
        assert isinstance(data["daily_active_users"], list)
        assert isinstance(data["daily_sessions"], list)
        assert isinstance(data["daily_tokens"], list)

    def test_trends_empty_for_no_data(self, admin_client):
        """All arrays are empty when no data in range."""
        resp = admin_client.get(
            "/api/admin/dashboard/trends?from=2020-01-01&to=2020-01-31"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["daily_active_users"] == []
        assert data["daily_sessions"] == []
        assert data["daily_tokens"] == []

    def test_trends_date_item_structure(self, admin_client):
        """Each item has date and count fields."""
        resp = admin_client.get("/api/admin/dashboard/trends?from=2026-01-01&to=2026-01-31")
        assert resp.status_code == 200
        data = resp.json()
        for item in data["daily_active_users"]:
            assert "date" in item
            assert "count" in item
        for item in data["daily_sessions"]:
            assert "date" in item
            assert "count" in item
        for item in data["daily_tokens"]:
            assert "date" in item
            assert "input" in item
            assert "output" in item
            assert "cache_read" in item
            assert "cache_write" in item
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_dashboard_api.py::TestDashboardTrends -v
```

Expected: FAIL — 404.

- [ ] **Step 3: Implement the trends endpoint**

Add in `main_server.py`, after the overview endpoint:

```python
@app.get("/api/admin/dashboard/trends")
async def dashboard_trends(
    from_date: str | None = None,
    to_date: str | None = None,
    current_user: str = Depends(require_admin),
) -> dict[str, Any]:
    """Daily time-series data for dashboard charts.

    Query params:
        from_date: Start date YYYY-MM-DD (default: 30 days ago)
        to_date:   End date YYYY-MM-DD (default: today)
    """
    from datetime import date, timedelta

    today = date.today()
    to_dt = date.fromisoformat(to_date) if to_date else today
    from_dt = date.fromisoformat(from_date) if from_date else today - timedelta(days=30)

    if from_dt > to_dt:
        raise HTTPException(status_code=422, detail="from_date must be <= to_date")
    if (to_dt - from_dt).days > 365:
        raise HTTPException(status_code=422, detail="Date range must not exceed 365 days")

    from_str = from_dt.isoformat()
    to_str = to_dt.isoformat()

    daily_active_users: list[dict] = []
    daily_sessions: list[dict] = []
    daily_tokens: list[dict] = []

    if _db is not None:
        async with _db.connection() as conn:
            # Daily active users
            cursor = await conn.execute(
                "SELECT date(last_active_at) as d, COUNT(DISTINCT user_id) "
                "FROM sessions WHERE last_active_at >= ? AND last_active_at <= ? "
                "GROUP BY d ORDER BY d",
                (from_str, to_str),
            )
            rows = await cursor.fetchall()
            daily_active_users = [{"date": r[0], "count": r[1]} for r in rows]

            # Daily sessions
            cursor = await conn.execute(
                "SELECT date(created_at) as d, COUNT(*) "
                "FROM sessions WHERE created_at >= ? AND created_at <= ? "
                "GROUP BY d ORDER BY d",
                (from_str, to_str),
            )
            rows = await cursor.fetchall()
            daily_sessions = [{"date": r[0], "count": r[1]} for r in rows]

            # Daily tokens (join messages with sessions)
            cursor = await conn.execute(
                "SELECT date(m.created_at) as d, "
                "COALESCE(SUM(CAST(json_extract(m.usage, '$.input_tokens') AS INTEGER)), 0), "
                "COALESCE(SUM(CAST(json_extract(m.usage, '$.output_tokens') AS INTEGER)), 0), "
                "COALESCE(SUM(CAST(json_extract(m.usage, '$.cache_read_tokens') AS INTEGER)), 0), "
                "COALESCE(SUM(CAST(json_extract(m.usage, '$.cache_write_tokens') AS INTEGER)), 0) "
                "FROM messages m "
                "JOIN sessions s ON m.session_id = s.session_id "
                "WHERE m.created_at >= ? AND m.created_at <= ? "
                "GROUP BY d ORDER BY d",
                (from_str, to_str),
            )
            rows = await cursor.fetchall()
            daily_tokens = [
                {
                    "date": r[0],
                    "input": r[1],
                    "output": r[2],
                    "cache_read": r[3],
                    "cache_write": r[4],
                }
                for r in rows
            ]

    return {
        "daily_active_users": daily_active_users,
        "daily_sessions": daily_sessions,
        "daily_tokens": daily_tokens,
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_dashboard_api.py::TestDashboardTrends -v
```

Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add main_server.py tests/unit/test_dashboard_api.py
git commit -m "feat: add dashboard trends API endpoint"
```

---

### Task 4: Dashboard rankings endpoint

**Files:**
- Modify: `main_server.py` (append after trends endpoint)
- Modify: `tests/unit/test_dashboard_api.py`

- [ ] **Step 1: Write failing test**

Append to `tests/unit/test_dashboard_api.py`:

```python
class TestDashboardRankings:
    def test_rankings_returns_expected_structure(self, admin_client):
        """Rankings endpoint returns top_users and top_skills lists."""
        resp = admin_client.get(
            "/api/admin/dashboard/rankings?from=2026-01-01&to=2026-01-31"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "top_users" in data
        assert "top_skills" in data
        assert isinstance(data["top_users"], list)
        assert isinstance(data["top_skills"], list)

    def test_rankings_empty_for_no_data(self, admin_client):
        """Empty lists when no data in range."""
        resp = admin_client.get(
            "/api/admin/dashboard/rankings?from=2020-01-01&to=2020-01-31"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["top_users"] == []
        assert data["top_skills"] == []

    def test_rankings_user_item_structure(self, admin_client):
        """Each user item has correct fields."""
        resp = admin_client.get("/api/admin/dashboard/rankings?from=2026-01-01&to=2026-01-31")
        assert resp.status_code == 200
        data = resp.json()
        for user in data["top_users"]:
            assert "user_id" in user
            assert "total_tokens" in user
            assert "session_count" in user

    def test_rankings_skill_item_structure(self, admin_client):
        """Each skill item has correct fields."""
        resp = admin_client.get("/api/admin/dashboard/rankings?from=2026-01-01&to=2026-01-31")
        assert resp.status_code == 200
        data = resp.json()
        for skill in data["top_skills"]:
            assert "skill_name" in skill
            assert "use_count" in skill
            assert "unique_users" in skill
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_dashboard_api.py::TestDashboardRankings -v
```

Expected: FAIL — 404.

- [ ] **Step 3: Implement the rankings endpoint**

Add in `main_server.py`, after the trends endpoint:

```python
@app.get("/api/admin/dashboard/rankings")
async def dashboard_rankings(
    from_date: str | None = None,
    to_date: str | None = None,
    current_user: str = Depends(require_admin),
) -> dict[str, Any]:
    """Top users by token consumption and top skills by usage count.

    Query params:
        from_date: Start date YYYY-MM-DD (default: 30 days ago)
        to_date:   End date YYYY-MM-DD (default: today)
    """
    from datetime import date, timedelta

    today = date.today()
    to_dt = date.fromisoformat(to_date) if to_date else today
    from_dt = date.fromisoformat(from_date) if from_date else today - timedelta(days=30)

    if from_dt > to_dt:
        raise HTTPException(status_code=422, detail="from_date must be <= to_date")
    if (to_dt - from_dt).days > 365:
        raise HTTPException(status_code=422, detail="Date range must not exceed 365 days")

    from_str = from_dt.isoformat()
    to_str = to_dt.isoformat()

    top_users: list[dict] = []
    top_skills: list[dict] = []

    if _db is not None:
        async with _db.connection() as conn:
            # Top users by total tokens (input + output + cache)
            cursor = await conn.execute(
                "SELECT s.user_id, "
                "COALESCE(SUM("
                "  CAST(json_extract(m.usage, '$.input_tokens') AS INTEGER) + "
                "  CAST(json_extract(m.usage, '$.output_tokens') AS INTEGER) + "
                "  CAST(json_extract(m.usage, '$.cache_read_tokens') AS INTEGER) + "
                "  CAST(json_extract(m.usage, '$.cache_write_tokens') AS INTEGER)"
                "), 0) as total_tokens, "
                "COUNT(DISTINCT s.session_id) as session_count "
                "FROM messages m "
                "JOIN sessions s ON m.session_id = s.session_id "
                "WHERE m.created_at >= ? AND m.created_at <= ? "
                "GROUP BY s.user_id "
                "ORDER BY total_tokens DESC LIMIT 10",
                (from_str, to_str),
            )
            rows = await cursor.fetchall()
            top_users = [
                {"user_id": r[0], "total_tokens": r[1], "session_count": r[2]}
                for r in rows
            ]

            # Top skills by usage count in range
            # Join skill_usage with sessions to filter by session creation date
            cursor = await conn.execute(
                "SELECT su.skill_name, COUNT(*) as use_count, "
                "COUNT(DISTINCT su.user_id) as unique_users "
                "FROM skill_usage su "
                "JOIN sessions s ON su.session_id = s.session_id "
                "WHERE su.created_at >= ? AND su.created_at <= ? "
                "AND su.session_id != '' "
                "GROUP BY su.skill_name "
                "ORDER BY use_count DESC LIMIT 10",
                (from_str, to_str),
            )
            rows = await cursor.fetchall()
            top_skills = [
                {"skill_name": r[0], "use_count": r[1], "unique_users": r[2]}
                for r in rows
            ]

    return {"top_users": top_users, "top_skills": top_skills}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_dashboard_api.py::TestDashboardRankings -v
```

Expected: PASS (4 tests).

- [ ] **Step 5: Run all backend dashboard tests together**

```bash
uv run pytest tests/unit/test_dashboard_api.py -v
```

Expected: All 12 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add main_server.py tests/unit/test_dashboard_api.py
git commit -m "feat: add dashboard rankings API endpoint"
```

---

### Task 5: Install Recharts and create `useDashboardApi` hook

**Files:**
- Create: `frontend/src/hooks/useDashboardApi.ts`

- [ ] **Step 1: Install Recharts**

```bash
cd frontend && npm install recharts
```

- [ ] **Step 2: Create the data fetching hook**

Create `frontend/src/hooks/useDashboardApi.ts`:

```typescript
import { useState, useEffect, useCallback, useMemo, useRef } from "react";

export interface OverviewData {
  active_users: number;
  total_users: number;
  new_users: number;
  total_sessions: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_read_tokens: number;
  total_cache_write_tokens: number;
}

export interface DailyCount {
  date: string;
  count: number;
}

export interface DailyTokens {
  date: string;
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
}

export interface TrendsData {
  daily_active_users: DailyCount[];
  daily_sessions: DailyCount[];
  daily_tokens: DailyTokens[];
}

export interface TopUser {
  user_id: string;
  total_tokens: number;
  session_count: number;
}

export interface TopSkill {
  skill_name: string;
  use_count: number;
  unique_users: number;
}

export interface RankingsData {
  top_users: TopUser[];
  top_skills: TopSkill[];
}

interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

interface DashboardApi {
  overview: AsyncState<OverviewData>;
  trends: AsyncState<TrendsData>;
  rankings: AsyncState<RankingsData>;
  refetch: (from: string, to: string) => void;
}

const API_BASE = "/api/admin/dashboard";

function formatDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function todayStr(): string {
  return formatDate(new Date());
}

function daysAgoStr(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return formatDate(d);
}

async function fetchJson<T>(url: string, token: string): Promise<T> {
  const resp = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!resp.ok) {
    throw new Error(`${resp.status} ${resp.statusText}`);
  }
  return resp.json() as Promise<T>;
}

export function useDashboardApi(initialFrom?: string, initialTo?: string): DashboardApi {
  const authToken = useMemo(() => localStorage.getItem("authToken") || "", []);
  const initialFromRef = useRef(initialFrom || daysAgoStr(30));
  const initialToRef = useRef(initialTo || todayStr());

  const [from, setFrom] = useState(initialFromRef.current);
  const [to, setTo] = useState(initialToRef.current);

  const [overview, setOverview] = useState<AsyncState<OverviewData>>({
    data: null, loading: true, error: null,
  });
  const [trends, setTrends] = useState<AsyncState<TrendsData>>({
    data: null, loading: true, error: null,
  });
  const [rankings, setRankings] = useState<AsyncState<RankingsData>>({
    data: null, loading: true, error: null,
  });

  const fetchAll = useCallback(
    (fromDate: string, toDate: string) => {
      setOverview((s) => ({ ...s, loading: true, error: null }));
      setTrends((s) => ({ ...s, loading: true, error: null }));
      setRankings((s) => ({ ...s, loading: true, error: null }));

      const params = `?from_date=${fromDate}&to_date=${toDate}`;

      fetchJson<OverviewData>(`${API_BASE}/overview${params}`, authToken)
        .then((data) => setOverview({ data, loading: false, error: null }))
        .catch((e) => setOverview({ data: null, loading: false, error: e.message }));

      fetchJson<TrendsData>(`${API_BASE}/trends${params}`, authToken)
        .then((data) => setTrends({ data, loading: false, error: null }))
        .catch((e) => setTrends({ data: null, loading: false, error: e.message }));

      fetchJson<RankingsData>(`${API_BASE}/rankings${params}`, authToken)
        .then((data) => setRankings({ data, loading: false, error: null }))
        .catch((e) => setRankings({ data: null, loading: false, error: e.message }));
    },
    [authToken],
  );

  useEffect(() => {
    fetchAll(from, to);
  }, [from, to, fetchAll]);

  const refetch = useCallback(
    (newFrom: string, newTo: string) => {
      setFrom(newFrom);
      setTo(newTo);
    },
    [],
  );

  return { overview, trends, rankings, refetch };
}
```

- [ ] **Step 3: Verify TypeScript compilation**

```bash
cd frontend && npx tsc --noEmit
```

Expected: No errors from the new hook file.

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/hooks/useDashboardApi.ts package.json package-lock.json
git commit -m "feat: add useDashboardApi hook and recharts dependency"
```

---

### Task 6: TimeRangeSelector component

**Files:**
- Create: `frontend/src/components/dashboard/TimeRangeSelector.tsx`
- Create: `frontend/src/components/dashboard/TimeRangeSelector.css`

- [ ] **Step 1: Create the component**

Create `frontend/src/components/dashboard/TimeRangeSelector.tsx`:

```tsx
import { useState } from "react";
import "./TimeRangeSelector.css";

function formatDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function today(): Date {
  return new Date();
}

function daysAgo(n: number): Date {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d;
}

export type TimePreset = "today" | "7d" | "30d" | "custom";

interface TimeRangeSelectorProps {
  from: string;
  to: string;
  onChange: (from: string, to: string) => void;
}

const PRESETS: { key: TimePreset; label: string; from: () => Date; to: () => Date }[] = [
  { key: "today", label: "Today", from: today, to: today },
  { key: "7d", label: "7 Days", from: () => daysAgo(7), to: today },
  { key: "30d", label: "30 Days", from: () => daysAgo(30), to: today },
];

export default function TimeRangeSelector({ from, to, onChange }: TimeRangeSelectorProps) {
  const [activePreset, setActivePreset] = useState<TimePreset>("30d");
  const [showCustom, setShowCustom] = useState(false);
  const [customFrom, setCustomFrom] = useState(from);
  const [customTo, setCustomTo] = useState(to);

  function applyPreset(preset: TimePreset) {
    setActivePreset(preset);
    setShowCustom(false);
    const presetDef = PRESETS.find((p) => p.key === preset);
    if (presetDef) {
      const newFrom = formatDate(presetDef.from());
      const newTo = formatDate(presetDef.to());
      onChange(newFrom, newTo);
    }
  }

  function applyCustom() {
    if (customFrom && customTo) {
      setActivePreset("custom");
      onChange(customFrom, customTo);
    }
  }

  return (
    <div className="time-range-selector">
      <div className="time-range-presets">
        {PRESETS.map((p) => (
          <button
            key={p.key}
            className={`time-range-btn ${activePreset === p.key ? "active" : ""}`}
            onClick={() => applyPreset(p.key)}
          >
            {p.label}
          </button>
        ))}
        <button
          className={`time-range-btn ${activePreset === "custom" ? "active" : ""}`}
          onClick={() => setShowCustom(!showCustom)}
        >
          Custom
        </button>
      </div>
      {showCustom && (
        <div className="time-range-custom">
          <label>
            From:
            <input
              type="date"
              value={customFrom}
              onChange={(e) => setCustomFrom(e.target.value)}
            />
          </label>
          <label>
            To:
            <input
              type="date"
              value={customTo}
              onChange={(e) => setCustomTo(e.target.value)}
            />
          </label>
          <button className="time-range-btn apply" onClick={applyCustom}>
            Apply
          </button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Create styles**

Create `frontend/src/components/dashboard/TimeRangeSelector.css`:

```css
.time-range-selector {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}

.time-range-presets {
  display: flex;
  gap: 4px;
}

.time-range-btn {
  padding: 6px 14px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 6px;
  background: var(--color-surface, #fff);
  color: var(--color-text, #1a202c);
  font-size: 0.8125rem;
  cursor: pointer;
  transition: background 150ms, border-color 150ms;
}

.time-range-btn:hover {
  background: var(--color-hover, #f7fafc);
}

.time-range-btn.active {
  background: var(--color-accent, #4f46e5);
  color: #fff;
  border-color: var(--color-accent, #4f46e5);
}

.time-range-custom {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: 8px;
}

.time-range-custom label {
  font-size: 0.8125rem;
  display: flex;
  align-items: center;
  gap: 4px;
}

.time-range-custom input[type="date"] {
  padding: 4px 8px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 4px;
  font-size: 0.8125rem;
  background: var(--color-surface, #fff);
  color: var(--color-text, #1a202c);
}

.time-range-btn.apply {
  background: var(--color-accent, #4f46e5);
  color: #fff;
  border-color: var(--color-accent, #4f46e5);
}
```

- [ ] **Step 3: Verify TypeScript compilation**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/components/dashboard/TimeRangeSelector.tsx src/components/dashboard/TimeRangeSelector.css
git commit -m "feat: add TimeRangeSelector component for dashboard"
```

---

### Task 7: OverviewCards component

**Files:**
- Create: `frontend/src/components/dashboard/OverviewCards.tsx`
- Create: `frontend/src/components/dashboard/OverviewCards.css`

- [ ] **Step 1: Create format utility**

Add to `frontend/src/components/dashboard/OverviewCards.tsx`:

```tsx
import type { OverviewData } from "../../hooks/useDashboardApi";
import "./OverviewCards.css";

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function formatDelta(current: number, previous: number): string {
  if (previous === 0) return "";
  const pct = ((current - previous) / previous) * 100;
  const sign = pct >= 0 ? "↑" : "↓";
  return `${sign}${Math.abs(pct).toFixed(0)}%`;
}

function deltaClass(current: number, previous: number): string {
  if (previous === 0) return "delta-neutral";
  return current >= previous ? "delta-up" : "delta-down";
}

interface OverviewCardsProps {
  data: OverviewData | null;
  previousData: OverviewData | null;
  loading: boolean;
  error: string | null;
}

export default function OverviewCards({ data, previousData, loading, error }: OverviewCardsProps) {
  if (error) {
    return <div className="overview-error">Failed to load overview: {error}</div>;
  }

  const totalTokens = data
    ? data.total_input_tokens + data.total_output_tokens + data.total_cache_read_tokens + data.total_cache_write_tokens
    : 0;
  const prevTotalTokens = previousData
    ? previousData.total_input_tokens + previousData.total_output_tokens + previousData.total_cache_read_tokens + previousData.total_cache_write_tokens
    : 0;

  const cards = [
    {
      label: "Active Users",
      value: loading ? "—" : String(data?.active_users ?? 0),
      delta: data && previousData ? formatDelta(data.active_users, previousData.active_users) : "",
      deltaCls: data && previousData ? deltaClass(data.active_users, previousData.active_users) : "",
    },
    {
      label: "Total Users",
      value: loading ? "—" : String(data?.total_users ?? 0),
      delta: "",
      deltaCls: "",
    },
    {
      label: "New Users",
      value: loading ? "—" : `+${data?.new_users ?? 0}`,
      delta: data && previousData ? formatDelta(data.new_users, previousData.new_users) : "",
      deltaCls: data && previousData ? deltaClass(data.new_users, previousData.new_users) : "",
    },
    {
      label: "Total Sessions",
      value: loading ? "—" : String(data?.total_sessions ?? 0),
      delta: data && previousData ? formatDelta(data.total_sessions, previousData.total_sessions) : "",
      deltaCls: data && previousData ? deltaClass(data.total_sessions, previousData.total_sessions) : "",
    },
    {
      label: "Token Usage",
      value: loading ? "—" : formatTokens(totalTokens),
      delta: data && previousData ? formatDelta(totalTokens, prevTotalTokens) : "",
      deltaCls: data && previousData ? deltaClass(totalTokens, prevTotalTokens) : "",
      detail: data ? `I ${formatTokens(data.total_input_tokens)}  O ${formatTokens(data.total_output_tokens)}` : "",
    },
  ];

  return (
    <div className="overview-cards">
      {cards.map((card) => (
        <div key={card.label} className={`overview-card ${loading ? "loading" : ""}`}>
          <div className="card-label">{card.label}</div>
          <div className="card-value">{card.value}</div>
          {card.detail && <div className="card-detail">{card.detail}</div>}
          {card.delta && <span className={`card-delta ${card.deltaCls}`}>{card.delta}</span>}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Create styles**

Create `frontend/src/components/dashboard/OverviewCards.css`:

```css
.overview-cards {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.overview-card {
  position: relative;
  padding: 20px;
  border-radius: 12px;
  background: var(--color-surface, #fff);
  border: 1px solid var(--color-border, #e2e8f0);
  transition: box-shadow 150ms;
}

.overview-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
}

.overview-card.loading {
  opacity: 0.6;
}

.card-label {
  font-size: 0.75rem;
  color: var(--color-text-muted, #718096);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 8px;
}

.card-value {
  font-size: 1.75rem;
  font-weight: 700;
  color: var(--color-text, #1a202c);
  line-height: 1.2;
}

.card-detail {
  font-size: 0.7rem;
  color: var(--color-text-muted, #718096);
  margin-top: 6px;
  font-family: monospace;
}

.card-delta {
  position: absolute;
  top: 12px;
  right: 14px;
  font-size: 0.75rem;
  font-weight: 600;
}

.delta-up {
  color: #16a34a;
}

.delta-down {
  color: #dc2626;
}

.delta-neutral {
  color: var(--color-text-muted, #718096);
}

.overview-error {
  padding: 16px;
  color: #dc2626;
  background: #fef2f2;
  border-radius: 8px;
  margin-bottom: 24px;
}

@media (max-width: 1024px) {
  .overview-cards {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 640px) {
  .overview-cards {
    grid-template-columns: repeat(2, 1fr);
  }
}
```

- [ ] **Step 3: Verify TypeScript**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/components/dashboard/OverviewCards.tsx src/components/dashboard/OverviewCards.css
git commit -m "feat: add OverviewCards component for dashboard"
```

---

### Task 8: TokenTrendChart component

**Files:**
- Create: `frontend/src/components/dashboard/TokenTrendChart.tsx`

- [ ] **Step 1: Create the component**

Create `frontend/src/components/dashboard/TokenTrendChart.tsx`:

```tsx
import { useEffect, useState, type ComponentType } from "react";
import type { DailyTokens } from "../../hooks/useDashboardApi";

interface TokenTrendChartProps {
  data: DailyTokens[];
  loading: boolean;
  error: string | null;
}

export default function TokenTrendChart({ data, loading, error }: TokenTrendChartProps) {
  const [ChartComponents, setChartComponents] = useState<{
    LineChart: ComponentType<any>;
    Line: ComponentType<any>;
    XAxis: ComponentType<any>;
    YAxis: ComponentType<any>;
    Tooltip: ComponentType<any>;
    ResponsiveContainer: ComponentType<any>;
    Legend: ComponentType<any>;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    import("recharts").then((mod) => {
      if (!cancelled) {
        setChartComponents({
          LineChart: mod.LineChart,
          Line: mod.Line,
          XAxis: mod.XAxis,
          YAxis: mod.YAxis,
          Tooltip: mod.Tooltip,
          ResponsiveContainer: mod.ResponsiveContainer,
          Legend: mod.Legend,
        });
      }
    });
    return () => { cancelled = true; };
  }, []);

  if (error) {
    return <div className="chart-error">Failed to load token trends: {error}</div>;
  }

  if (loading || !ChartComponents) {
    return <div className="chart-loading">Loading chart...</div>;
  }

  if (data.length === 0) {
    return <div className="chart-empty">No token data for selected period</div>;
  }

  const { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } = ChartComponents;

  return (
    <div className="dashboard-chart">
      <h3 className="chart-title">Token Consumption Trends</h3>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
          <XAxis dataKey="date" tick={{ fontSize: 12 }} />
          <YAxis tick={{ fontSize: 12 }} tickFormatter={(v: number) => `${(v / 1000).toFixed(0)}K`} />
          <Tooltip
            formatter={(value: number) => [value.toLocaleString(), undefined]}
            labelStyle={{ fontSize: 12 }}
          />
          <Legend />
          <Line type="monotone" dataKey="input" name="Input" stroke="#4f46e5" strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="output" name="Output" stroke="#16a34a" strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="cache_read" name="Cache Read" stroke="#f59e0b" strokeWidth={2} dot={false} strokeDasharray="4 4" />
          <Line type="monotone" dataKey="cache_write" name="Cache Write" stroke="#dc2626" strokeWidth={2} dot={false} strokeDasharray="2 2" />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
cd frontend && git add src/components/dashboard/TokenTrendChart.tsx
git commit -m "feat: add TokenTrendChart component for dashboard"
```

---

### Task 9: ActivityTrendChart component

**Files:**
- Create: `frontend/src/components/dashboard/ActivityTrendChart.tsx`

- [ ] **Step 1: Create the component**

Create `frontend/src/components/dashboard/ActivityTrendChart.tsx`:

```tsx
import { useEffect, useState, type ComponentType } from "react";
import type { DailyCount } from "../../hooks/useDashboardApi";

interface ActivityTrendChartProps {
  dauData: DailyCount[];
  sessionsData: DailyCount[];
  loading: boolean;
  error: string | null;
}

export default function ActivityTrendChart({ dauData, sessionsData, loading, error }: ActivityTrendChartProps) {
  const [ChartComponents, setChartComponents] = useState<{
    LineChart: ComponentType<any>;
    Line: ComponentType<any>;
    XAxis: ComponentType<any>;
    YAxis: ComponentType<any>;
    Tooltip: ComponentType<any>;
    ResponsiveContainer: ComponentType<any>;
    Legend: ComponentType<any>;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    import("recharts").then((mod) => {
      if (!cancelled) {
        setChartComponents({
          LineChart: mod.LineChart,
          Line: mod.Line,
          XAxis: mod.XAxis,
          YAxis: mod.YAxis,
          Tooltip: mod.Tooltip,
          ResponsiveContainer: mod.ResponsiveContainer,
          Legend: mod.Legend,
        });
      }
    });
    return () => { cancelled = true; };
  }, []);

  if (error) {
    return <div className="chart-error">Failed to load activity trends: {error}</div>;
  }

  if (loading || !ChartComponents) {
    return <div className="chart-loading">Loading chart...</div>;
  }

  if (dauData.length === 0 && sessionsData.length === 0) {
    return <div className="chart-empty">No activity data for selected period</div>;
  }

  // Merge DAU and session data by date
  const merged = dauData.map((d) => {
    const session = sessionsData.find((s) => s.date === d.date);
    return { date: d.date, dau: d.count, sessions: session?.count ?? 0 };
  });

  if (merged.length === 0 && sessionsData.length > 0) {
    sessionsData.forEach((s) => {
      if (!merged.find((m) => m.date === s.date)) {
        merged.push({ date: s.date, dau: 0, sessions: s.count });
      }
    });
  }

  const { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } = ChartComponents;

  return (
    <div className="dashboard-chart">
      <h3 className="chart-title">Active Users & Sessions</h3>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={merged} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
          <XAxis dataKey="date" tick={{ fontSize: 12 }} />
          <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
          <Tooltip labelStyle={{ fontSize: 12 }} />
          <Legend />
          <Line type="monotone" dataKey="dau" name="Daily Active Users" stroke="#4f46e5" strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="sessions" name="Sessions" stroke="#16a34a" strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
cd frontend && git add src/components/dashboard/ActivityTrendChart.tsx
git commit -m "feat: add ActivityTrendChart component for dashboard"
```

---

### Task 10: UserRankingTable component

**Files:**
- Create: `frontend/src/components/dashboard/UserRankingTable.tsx`

- [ ] **Step 1: Create the component**

Create `frontend/src/components/dashboard/UserRankingTable.tsx`:

```tsx
import type { TopUser } from "../../hooks/useDashboardApi";

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

interface UserRankingTableProps {
  data: TopUser[];
  loading: boolean;
  error: string | null;
}

export default function UserRankingTable({ data, loading, error }: UserRankingTableProps) {
  if (error) {
    return <div className="ranking-error">Failed to load user rankings: {error}</div>;
  }

  return (
    <div className="ranking-panel">
      <h3 className="ranking-title">Top Users by Token Consumption</h3>
      {loading ? (
        <div className="ranking-loading">Loading...</div>
      ) : data.length === 0 ? (
        <div className="ranking-empty">No data for selected period</div>
      ) : (
        <table className="ranking-table">
          <thead>
            <tr>
              <th>#</th>
              <th>User</th>
              <th className="right">Tokens</th>
              <th className="right">Sessions</th>
            </tr>
          </thead>
          <tbody>
            {data.map((user, i) => (
              <tr key={user.user_id}>
                <td className="rank">{i + 1}</td>
                <td>{user.user_id}</td>
                <td className="right mono">{formatTokens(user.total_tokens)}</td>
                <td className="right">{user.session_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
cd frontend && git add src/components/dashboard/UserRankingTable.tsx
git commit -m "feat: add UserRankingTable component for dashboard"
```

---

### Task 11: SkillRankingTable component

**Files:**
- Create: `frontend/src/components/dashboard/SkillRankingTable.tsx`

- [ ] **Step 1: Create the component**

Create `frontend/src/components/dashboard/SkillRankingTable.tsx`:

```tsx
import type { TopSkill } from "../../hooks/useDashboardApi";

interface SkillRankingTableProps {
  data: TopSkill[];
  loading: boolean;
  error: string | null;
}

export default function SkillRankingTable({ data, loading, error }: SkillRankingTableProps) {
  if (error) {
    return <div className="ranking-error">Failed to load skill rankings: {error}</div>;
  }

  return (
    <div className="ranking-panel">
      <h3 className="ranking-title">Top Skills by Usage</h3>
      {loading ? (
        <div className="ranking-loading">Loading...</div>
      ) : data.length === 0 ? (
        <div className="ranking-empty">No data for selected period</div>
      ) : (
        <table className="ranking-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Skill</th>
              <th className="right">Uses</th>
              <th className="right">Users</th>
            </tr>
          </thead>
          <tbody>
            {data.map((skill, i) => (
              <tr key={skill.skill_name}>
                <td className="rank">{i + 1}</td>
                <td>{skill.skill_name}</td>
                <td className="right">{skill.use_count}</td>
                <td className="right">{skill.unique_users}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
cd frontend && git add src/components/dashboard/SkillRankingTable.tsx
git commit -m "feat: add SkillRankingTable component for dashboard"
```

---

### Task 12: ResourcePanel component

**Files:**
- Create: `frontend/src/components/dashboard/ResourcePanel.tsx`
- Create: `frontend/src/components/dashboard/ResourcePanel.css`

- [ ] **Step 1: Create the component**

Create `frontend/src/components/dashboard/ResourcePanel.tsx`:

```tsx
import { useState, useEffect } from "react";
import "./ResourcePanel.css";

interface ContainerInfo {
  container: {
    cpu_percent: number;
    memory_usage_mb: number;
    status: string;
  } | null;
  disk: {
    used_gb: number;
    total_gb: number;
  } | null;
  quota: Record<string, any> | null;
}

interface ResourcesData {
  [userId: string]: ContainerInfo;
}

function containerStatus(info: ContainerInfo): "normal" | "high-load" | "idle" {
  const cpu = info.container?.cpu_percent ?? 0;
  if (cpu > 80) return "high-load";
  if (cpu < 0.5) return "idle";
  return "normal";
}

function statusDot(status: string): string {
  if (status === "high-load") return "⚠";
  if (status === "idle") return "○";
  return "●";
}

export default function ResourcePanel() {
  const [data, setData] = useState<ResourcesData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const token = localStorage.getItem("authToken") || "";

  useEffect(() => {
    fetch("/api/admin/resources", {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status}`);
        return r.json();
      })
      .then((d) => {
        if (d.status === "container_mode_disabled" || d.status === "error") {
          setData(null);
          setError(d.detail || "Container mode not available");
        } else {
          setData(d);
        }
        setLoading(false);
      })
      .catch((e) => {
        setError(e.message);
        setLoading(false);
      });
  }, [token]);

  if (loading) return <div className="resource-loading">Loading resources...</div>;
  if (error) return <div className="resource-empty">Resources unavailable: {error}</div>;
  if (!data || Object.keys(data).length === 0) {
    return <div className="resource-empty">No running containers</div>;
  }

  const entries = Object.entries(data);
  const totalCpu = entries.reduce((sum, [, v]) => sum + (v.container?.cpu_percent ?? 0), 0);
  const totalMem = entries.reduce((sum, [, v]) => sum + (v.container?.memory_usage_mb ?? 0), 0);
  const totalDisk = entries.reduce((sum, [, v]) => sum + (v.disk?.used_gb ?? 0), 0);
  const totalDiskMax = entries.reduce((sum, [, v]) => sum + (v.disk?.total_gb ?? 0), 0);

  return (
    <div className="resource-panel">
      <h3 className="chart-title">Container Resources</h3>
      <div className="resource-summary">
        <span className="resource-stat">● Running: {entries.length}</span>
        <span className="resource-stat">CPU: {totalCpu.toFixed(1)}%</span>
        <span className="resource-stat">
          Mem: {(totalMem / 1024).toFixed(1)} / {(totalMem / 1024).toFixed(1)} GB
        </span>
        <span className="resource-stat">
          Disk: {totalDisk.toFixed(1)} / {totalDiskMax.toFixed(0)} GB
        </span>
      </div>
      <table className="resource-table">
        <thead>
          <tr>
            <th>User</th>
            <th>Container</th>
            <th className="right">CPU</th>
            <th className="right">Mem</th>
            <th className="right">Disk</th>
            <th className="center">St</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([userId, info]) => {
            const st = containerStatus(info);
            return (
              <tr key={userId}>
                <td>{userId}</td>
                <td className="mono">web-agent-{userId}</td>
                <td className="right">{info.container?.cpu_percent?.toFixed(1) ?? "—"}%</td>
                <td className="right">
                  {info.container?.memory_usage_mb != null
                    ? `${info.container.memory_usage_mb.toFixed(0)}MB`
                    : "—"}
                </td>
                <td className="right">
                  {info.disk ? `${info.disk.used_gb.toFixed(1)}GB` : "—"}
                </td>
                <td className={`center status-${st}`}>{statusDot(st)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: Create styles**

Create `frontend/src/components/dashboard/ResourcePanel.css`:

```css
.resource-panel {
  margin-top: 24px;
}

.resource-summary {
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
  margin-bottom: 12px;
  padding: 12px 16px;
  background: var(--color-surface-alt, #f8fafc);
  border-radius: 8px;
  font-size: 0.8125rem;
}

.resource-stat {
  color: var(--color-text, #1a202c);
}

.resource-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8125rem;
}

.resource-table th,
.resource-table td {
  padding: 8px 12px;
  text-align: left;
  border-bottom: 1px solid var(--color-border, #e2e8f0);
}

.resource-table th {
  font-weight: 600;
  color: var(--color-text-muted, #718096);
}

.resource-table .right {
  text-align: right;
}

.resource-table .center {
  text-align: center;
}

.status-high-load {
  color: #dc2626;
}

.status-idle {
  color: #9ca3af;
}

.status-normal {
  color: #16a34a;
}

.resource-loading,
.resource-empty {
  padding: 16px;
  color: var(--color-text-muted, #718096);
  text-align: center;
}
```

- [ ] **Step 3: Verify TypeScript**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/components/dashboard/ResourcePanel.tsx src/components/dashboard/ResourcePanel.css
git commit -m "feat: add ResourcePanel component for dashboard"
```

---

### Task 13: DashboardPage, routing, and SettingsMenu integration

**Files:**
- Create: `frontend/src/components/DashboardPage.tsx`
- Create: `frontend/src/components/dashboard/dashboard.css`
- Modify: `frontend/src/App.tsx:30,1547,1672`
- Modify: `frontend/src/components/SettingsMenu.tsx:12,49`

- [ ] **Step 1: Create shared dashboard styles**

Create `frontend/src/components/dashboard/dashboard.css`:

```css
.dashboard-page {
  max-width: 1200px;
  margin: 0 auto;
  padding: 32px 24px;
}

.dashboard-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 28px;
  flex-wrap: wrap;
  gap: 12px;
}

.dashboard-header h2 {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--color-text, #1a202c);
  margin: 0;
}

.dashboard-back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.875rem;
  color: var(--color-text-muted, #718096);
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px 0;
  margin-bottom: 16px;
}

.dashboard-back:hover {
  color: var(--color-text, #1a202c);
}

.dashboard-chart {
  background: var(--color-surface, #fff);
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 12px;
  padding: 20px;
  margin-bottom: 24px;
}

.chart-title {
  font-size: 0.9375rem;
  font-weight: 600;
  margin: 0 0 16px 0;
  color: var(--color-text, #1a202c);
}

.chart-loading,
.chart-empty,
.chart-error {
  padding: 40px;
  text-align: center;
  color: var(--color-text-muted, #718096);
}

.chart-error {
  color: #dc2626;
}

.rankings-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  margin-bottom: 24px;
}

.ranking-panel {
  background: var(--color-surface, #fff);
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 12px;
  padding: 20px;
}

.ranking-title {
  font-size: 0.9375rem;
  font-weight: 600;
  margin: 0 0 16px 0;
  color: var(--color-text, #1a202c);
}

.ranking-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8125rem;
}

.ranking-table th,
.ranking-table td {
  padding: 8px 12px;
  text-align: left;
  border-bottom: 1px solid var(--color-border, #e2e8f0);
}

.ranking-table th {
  font-weight: 600;
  color: var(--color-text-muted, #718096);
}

.ranking-table .right {
  text-align: right;
}

.ranking-table .rank {
  width: 32px;
  color: var(--color-text-muted, #718096);
}

.ranking-table .mono {
  font-family: monospace;
  font-size: 0.75rem;
}

.ranking-loading,
.ranking-empty,
.ranking-error {
  padding: 20px;
  text-align: center;
  color: var(--color-text-muted, #718096);
}

.ranking-error {
  color: #dc2626;
}

@media (max-width: 768px) {
  .rankings-row {
    grid-template-columns: 1fr;
  }

  .dashboard-page {
    padding: 20px 16px;
  }
}
```

- [ ] **Step 2: Create DashboardPage**

Create `frontend/src/components/DashboardPage.tsx`:

```tsx
import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useDashboardApi } from "../hooks/useDashboardApi";
import TimeRangeSelector from "./dashboard/TimeRangeSelector";
import OverviewCards from "./dashboard/OverviewCards";
import TokenTrendChart from "./dashboard/TokenTrendChart";
import ActivityTrendChart from "./dashboard/ActivityTrendChart";
import UserRankingTable from "./dashboard/UserRankingTable";
import SkillRankingTable from "./dashboard/SkillRankingTable";
import ResourcePanel from "./dashboard/ResourcePanel";
import "./dashboard/dashboard.css";

function formatDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function todayStr(): string {
  return formatDate(new Date());
}

function daysAgoStr(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return formatDate(d);
}

export default function DashboardPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [timeRange, setTimeRange] = useState({ from: daysAgoStr(30), to: todayStr() });

  const api = useDashboardApi(timeRange.from, timeRange.to);

  // Fetch previous period for deltas (same length, before current range)
  const rangeDays = Math.round(
    (new Date(timeRange.to).getTime() - new Date(timeRange.from).getTime()) /
      (1000 * 60 * 60 * 24),
  );
  const prevFrom = daysAgoStr(rangeDays * 2);
  const prevTo = daysAgoStr(rangeDays + 1);

  // Only fetch previous period overview for deltas
  const [prevOverview, setPrevOverview] = useState<any>(null);

  useEffect(() => {
    const token = localStorage.getItem("authToken") || "";
    const params = `?from_date=${prevFrom}&to_date=${prevTo}`;
    fetch(`/api/admin/dashboard/overview${params}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((r) => r.json())
      .then(setPrevOverview)
      .catch(() => setPrevOverview(null));
  }, [prevFrom, prevTo]);

  function handleTimeChange(from: string, to: string) {
    setTimeRange({ from, to });
    api.refetch(from, to);
  }

  return (
    <div className="dashboard-page">
      <button className="dashboard-back" onClick={() => navigate("/")}>
        ← Back
      </button>

      <div className="dashboard-header">
        <h2>{t("dashboard.title", "Usage Dashboard")}</h2>
        <TimeRangeSelector
          from={timeRange.from}
          to={timeRange.to}
          onChange={handleTimeChange}
        />
      </div>

      <OverviewCards
        data={api.overview.data}
        previousData={prevOverview}
        loading={api.overview.loading}
        error={api.overview.error}
      />

      <TokenTrendChart
        data={api.trends.data?.daily_tokens ?? []}
        loading={api.trends.loading}
        error={api.trends.error}
      />

      <ActivityTrendChart
        dauData={api.trends.data?.daily_active_users ?? []}
        sessionsData={api.trends.data?.daily_sessions ?? []}
        loading={api.trends.loading}
        error={api.trends.error}
      />

      <div className="rankings-row">
        <UserRankingTable
          data={api.rankings.data?.top_users ?? []}
          loading={api.rankings.loading}
          error={api.rankings.error}
        />
        <SkillRankingTable
          data={api.rankings.data?.top_skills ?? []}
          loading={api.rankings.loading}
          error={api.rankings.error}
        />
      </div>

      <ResourcePanel />
    </div>
  );
}
```

- [ ] **Step 3: Add DashboardPage import to App.tsx**

In `frontend/src/App.tsx`, add the import after the existing component imports (after line 17):

```typescript
import DashboardPage from "./components/DashboardPage";
```

- [ ] **Step 4: Add `/dashboard` route to App.tsx**

In `frontend/src/App.tsx`, in the `MainApp` Routes block (after line 1588, which is the end of the `/evolution` route):

```tsx
<Route
  path="/dashboard"
  element={
    userRole === "admin" ? (
      <DashboardPage />
    ) : (
      <Navigate to="/" replace />
    )
  }
/>
```

Note: `Navigate` is already imported from `react-router-dom` (check line 2).

- [ ] **Step 5: Add SettingsMenu entry**

In `frontend/src/components/SettingsMenu.tsx`:

The component currently accepts `{ onOpenSkills, onOpenFeedback, onOpenEvolution, onOpenMCP, userRole }`. Add an `onOpenDashboard` prop.

Update the interface (line 4-10):

```typescript
interface SettingsMenuProps {
  onOpenSkills: () => void;
  onOpenFeedback: () => void;
  onOpenEvolution: () => void;
  onOpenMCP: () => void;
  onOpenDashboard: () => void;
  userRole: string;
}
```

Update component function signature (line 12):

```typescript
export default function SettingsMenu({
  onOpenSkills,
  onOpenFeedback,
  onOpenEvolution,
  onOpenMCP,
  onOpenDashboard,
  userRole,
}: SettingsMenuProps) {
```

Add the Dashboard menu item before the existing admin items (after line 48, before MCP Servers):

```tsx
{isAdmin && (
  <button onClick={onOpenDashboard} className="settings-item">
    <span className="settings-icon">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
      </svg>
    </span>
    Usage Dashboard
  </button>
)}
```

- [ ] **Step 6: Update SettingsMenu usage in App.tsx**

In `frontend/src/App.tsx` around line 277-280, add the `onOpenDashboard` callback:

```tsx
onOpenMCP={() => navigate("/mcp")}
onOpenDashboard={() => navigate("/dashboard")}   // <-- add this line
```

- [ ] **Step 7: Verify TypeScript compilation**

```bash
cd frontend && npx tsc --noEmit
```

Expected: No errors.

- [ ] **Step 8: Commit**

```bash
cd frontend && git add \
  src/components/DashboardPage.tsx \
  src/components/dashboard/dashboard.css \
  src/App.tsx \
  src/components/SettingsMenu.tsx
git commit -m "feat: add DashboardPage with routing and settings menu entry"
```

---

## Verification

After implementing all tasks, verify end-to-end:

1. **Backend APIs:**
   ```bash
   # Run all dashboard tests
   uv run pytest tests/unit/test_dashboard_api.py -v

   # Start dev server and test manually
   curl "http://localhost:8000/api/admin/dashboard/overview?from_date=2026-05-01&to_date=2026-05-20"
   curl "http://localhost:8000/api/admin/dashboard/trends?from_date=2026-05-01&to_date=2026-05-20"
   curl "http://localhost:8000/api/admin/dashboard/rankings?from_date=2026-05-01&to_date=2026-05-20"
   ```

2. **Data fix:**
   - Run a session, then check SQLite: `SELECT usage FROM messages WHERE usage IS NOT NULL LIMIT 1;`
   - Verify `model` field exists in the JSON

3. **Frontend:**
   - Start frontend dev server: `cd frontend && npm run dev`
   - Login as admin, open Settings menu → "Usage Dashboard"
   - Verify page loads with all sections
   - Click time presets (Today, 7 Days, 30 Days) — charts/tables refresh
   - Use Custom date range picker — verify correct date filtering
   - Verify non-admin user has no "Usage Dashboard" menu item and gets redirected from `/dashboard`

4. **Edge cases:**
   - Empty data period: all cards show 0, charts show empty state message
   - API error: individual section shows error state, other sections unaffected
   - Container mode disabled: ResourcePanel shows "Container mode not available"
   - Single user: rankings show only that user

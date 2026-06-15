# User Management Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an admin-only user management page at `/users` with list/search/filter/pagination and disable/enable/promote/demote actions.

**Architecture:** Five FastAPI endpoints under `/api/admin/users` with `require_admin` guard, querying SQLite `users` table with aggregated session/message stats. React frontend follows the existing `.detail-page` / `.ranking-panel` / `.ranking-table` CSS patterns and `useEvolutionApi` hook pattern.

**Tech Stack:** FastAPI + aiosqlite (backend), React + TypeScript + Vitest (frontend), i18next (translations)

---

### Task 1: Backend — User listing endpoint

**Files:**
- Modify: `main_server.py` — append new endpoints before the `if __name__` block
- Create: `tests/unit/test_user_management.py`

- [ ] **Step 1: Write failing test for GET /api/admin/users**

Create `tests/unit/test_user_management.py`:

```python
"""Tests for /api/admin/users endpoints."""
import time
import pytest
from fastapi.testclient import TestClient


def test_list_users_returns_paginated_items(client: TestClient):
    resp = client.get("/api/admin/users?page=1&page_size=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "items" in body["data"]
    assert "total" in body["data"]
    assert body["data"]["page"] == 1
    assert body["data"]["page_size"] == 10


def test_list_users_search_by_user_id(client: TestClient):
    resp = client.get("/api/admin/users?q=admin")
    assert resp.status_code == 200
    body = resp.json()
    for item in body["data"]["items"]:
        assert "admin" in item["user_id"].lower()


def test_list_users_filter_by_role(client: TestClient):
    resp = client.get("/api/admin/users?role=admin")
    assert resp.status_code == 200
    for item in resp.json()["data"]["items"]:
        assert item["role"] == "admin"


def test_list_users_filter_by_status(client: TestClient):
    resp = client.get("/api/admin/users?status=disabled")
    assert resp.status_code == 200
    for item in resp.json()["data"]["items"]:
        assert item["status"] == "disabled"


def test_list_users_sort_by_last_active(client: TestClient):
    resp = client.get("/api/admin/users?sort=last_active_at&order=desc&page_size=50")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    if len(items) >= 2:
        assert items[0]["last_active_at"] >= items[-1]["last_active_at"]


def test_list_users_rejects_invalid_sort_column(client: TestClient):
    resp = client.get("/api/admin/users?sort=password_hash&order=asc")
    assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_user_management.py -v
```

Expected: all tests FAIL (404 or assertion errors).

- [ ] **Step 3: Add GET /api/admin/users endpoint**

In `main_server.py`, after the last existing admin endpoint, add:

```python
# ── User Management ──────────────────────────────────────────────

_ALLOWED_USER_SORT_COLUMNS = frozenset(
    {"user_id", "role", "status", "created_at", "last_active_at"}
)


@app.get("/api/admin/users")
async def admin_list_users(
    q: str = "",
    role: str = "",
    status: str = "",
    sort: str = "created_at",
    order: str = "desc",
    page: int = 1,
    page_size: int = 20,
    current_user: str = Depends(require_admin),
):
    if sort not in _ALLOWED_USER_SORT_COLUMNS:
        raise HTTPException(400, f"Invalid sort column: {sort}")
    if order not in ("asc", "desc"):
        raise HTTPException(400, "order must be 'asc' or 'desc'")

    conditions: list[str] = []
    params: list[str | int] = []

    if q:
        conditions.append("u.user_id LIKE ?")
        params.append(f"%{q}%")
    if role:
        conditions.append("u.role = ?")
        params.append(role)
    if status:
        conditions.append("u.status = ?")
        params.append(status)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.extend([page_size, (page - 1) * page_size])

    async with _db.connection() as conn:
        count_row = await conn.fetchone(
            f"SELECT COUNT(*) FROM users u {where_clause}",
            params[:-2] if conditions else [],
        )
        total = count_row[0] if count_row else 0

        rows = await conn.fetchall(
            f"""
            SELECT u.user_id, u.role, u.status, u.created_at, u.last_active_at,
                   u.disabled_at, u.disabled_by,
                   (SELECT COUNT(*) FROM sessions WHERE user_id = u.user_id) AS session_count,
                   (SELECT COALESCE(SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens), 0)
                    FROM messages WHERE user_id = u.user_id) AS total_tokens
            FROM users u
            {where_clause}
            ORDER BY u.{sort} {order}
            LIMIT ? OFFSET ?
            """,
            params,
        )

    items = [
        {
            "user_id": r[0],
            "role": r[1],
            "status": r[2],
            "created_at": r[3],
            "last_active_at": r[4],
            "disabled_at": r[5],
            "disabled_by": r[6],
            "session_count": r[7],
            "total_tokens": r[8],
        }
        for r in rows
    ]

    return {
        "success": True,
        "data": {"items": items, "total": total, "page": page, "page_size": page_size},
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_user_management.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add main_server.py tests/unit/test_user_management.py
git commit -m "feat: add GET /api/admin/users with search, filter, sort, pagination"
```

---

### Task 2: Backend — Disable / Enable endpoints

**Files:**
- Modify: `tests/unit/test_user_management.py`
- Modify: `main_server.py`

- [ ] **Step 1: Write failing tests for disable/enable**

Append to `tests/unit/test_user_management.py`:

```python
def test_disable_user_sets_status(client: TestClient):
    resp = client.post("/api/admin/users/testuser/disable")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["status"] == "disabled"
    assert body["data"]["disabled_by"] is not None
    assert body["data"]["disabled_at"] is not None


def test_disable_user_already_disabled_returns_409(client: TestClient):
    # second call on already-disabled user
    resp = client.post("/api/admin/users/testuser/disable")
    assert resp.status_code == 409


def test_disable_self_returns_403(client: TestClient):
    resp = client.post("/api/admin/users/default/disable")
    assert resp.status_code == 403


def test_enable_user_clears_disabled_fields(client: TestClient):
    resp = client.post("/api/admin/users/testuser/enable")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["status"] == "active"
    assert body["data"]["disabled_at"] is None
    assert body["data"]["disabled_by"] is None


def test_enable_user_already_active_returns_409(client: TestClient):
    resp = client.post("/api/admin/users/testuser/enable")
    assert resp.status_code == 409


def test_non_admin_rejected(client_no_auth: TestClient):
    resp = client_no_auth.get("/api/admin/users")
    assert resp.status_code in (401, 403)
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/unit/test_user_management.py::test_disable_user_sets_status -v
```

Expected: FAIL with 404.

- [ ] **Step 3: Add disable/enable endpoints**

Append in `main_server.py` after the GET endpoint:

```python
@app.post("/api/admin/users/{user_id}/disable")
async def admin_disable_user(
    user_id: str,
    current_user: str = Depends(require_admin),
):
    if user_id == current_user:
        raise HTTPException(403, "Cannot disable your own account")

    async with _db.connection() as conn:
        row = await conn.fetchone(
            "SELECT status FROM users WHERE user_id = ?", (user_id,)
        )
        if row is None:
            raise HTTPException(404, "User not found")
        if row[0] == "disabled":
            raise HTTPException(409, "User is already disabled")

        now = time.time()
        await conn.execute(
            "UPDATE users SET status = 'disabled', disabled_at = ?, disabled_by = ? WHERE user_id = ?",
            (now, current_user, user_id),
        )

        updated = await conn.fetchone(
            "SELECT user_id, role, status, created_at, last_active_at, disabled_at, disabled_by FROM users WHERE user_id = ?",
            (user_id,),
        )

    return {
        "success": True,
        "data": _row_to_user_dict(updated),
    }


@app.post("/api/admin/users/{user_id}/enable")
async def admin_enable_user(
    user_id: str,
    current_user: str = Depends(require_admin),
):
    async with _db.connection() as conn:
        row = await conn.fetchone(
            "SELECT status FROM users WHERE user_id = ?", (user_id,)
        )
        if row is None:
            raise HTTPException(404, "User not found")
        if row[0] == "active":
            raise HTTPException(409, "User is already active")

        await conn.execute(
            "UPDATE users SET status = 'active', disabled_at = NULL, disabled_by = NULL WHERE user_id = ?",
            (user_id,),
        )

        updated = await conn.fetchone(
            "SELECT user_id, role, status, created_at, last_active_at, disabled_at, disabled_by FROM users WHERE user_id = ?",
            (user_id,),
        )

    return {
        "success": True,
        "data": _row_to_user_dict(updated),
    }
```

- [ ] **Step 4: Add the row-to-dict helper before the GET endpoint**

Insert before the GET `/api/admin/users` endpoint:

```python
def _row_to_user_dict(row: sqlite3.Row | tuple) -> dict:
    return {
        "user_id": row[0],
        "role": row[1],
        "status": row[2],
        "created_at": row[3],
        "last_active_at": row[4],
        "disabled_at": row[5] if len(row) > 5 else None,
        "disabled_by": row[6] if len(row) > 6 else None,
    }
```

And add the sqlite3 import at the top of the file (it should already be imported).

- [ ] **Step 5: Add the `client_no_auth` fixture to conftest**

In `tests/unit/test_user_management.py`, the `client_no_auth` needs to be a fixture that doesn't send the admin header. Since the test client in dev mode (`ENFORCE_AUTH=false`) always passes `require_admin`, we need a different approach — create a test fixture that patches `ENFORCE_AUTH` to `True` for the no-auth test, or skip that test in dev mode:

```python
import os
import pytest


@pytest.fixture
def client_no_auth():
    """TestClient without admin token — only works when ENFORCE_AUTH=true."""
    if not os.getenv("ENFORCE_AUTH", "false").lower() == "true":
        pytest.skip("ENFORCE_AUTH is disabled; cannot test auth rejection")
    from fastapi.testclient import TestClient
    from main_server import app
    return TestClient(app)
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/unit/test_user_management.py -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add main_server.py tests/unit/test_user_management.py
git commit -m "feat: add POST /api/admin/users/{id}/disable and /enable endpoints"
```

---

### Task 3: Backend — Promote / Demote endpoints

**Files:**
- Modify: `tests/unit/test_user_management.py`
- Modify: `main_server.py`

- [ ] **Step 1: Write failing tests for promote/demote**

Append to `tests/unit/test_user_management.py`:

```python
def test_promote_user_to_admin(client: TestClient):
    # first ensure user is active
    client.post("/api/admin/users/testuser/enable")
    resp = client.post("/api/admin/users/testuser/promote")
    assert resp.status_code == 200
    assert resp.json()["data"]["role"] == "admin"


def test_promote_already_admin_returns_409(client: TestClient):
    resp = client.post("/api/admin/users/testuser/promote")
    assert resp.status_code == 409


def test_promote_disabled_user_returns_409(client: TestClient):
    client.post("/api/admin/users/testuser/disable")
    resp = client.post("/api/admin/users/testuser/promote")
    assert resp.status_code == 409
    # clean up
    client.post("/api/admin/users/testuser/enable")


def test_demote_user_from_admin(client: TestClient):
    resp = client.post("/api/admin/users/testuser/demote")
    assert resp.status_code == 200
    assert resp.json()["data"]["role"] == "user"


def test_demote_non_admin_returns_409(client: TestClient):
    resp = client.post("/api/admin/users/testuser/demote")
    assert resp.status_code == 409


def test_demote_self_returns_403(client: TestClient):
    resp = client.post("/api/admin/users/default/demote")
    assert resp.status_code == 403
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/unit/test_user_management.py::test_promote_user_to_admin -v
```

Expected: FAIL with 404.

- [ ] **Step 3: Add promote/demote endpoints**

Append in `main_server.py`:

```python
@app.post("/api/admin/users/{user_id}/promote")
async def admin_promote_user(
    user_id: str,
    current_user: str = Depends(require_admin),
):
    async with _db.connection() as conn:
        row = await conn.fetchone(
            "SELECT role, status FROM users WHERE user_id = ?", (user_id,)
        )
        if row is None:
            raise HTTPException(404, "User not found")
        if row[0] == "admin":
            raise HTTPException(409, "User is already an admin")
        if row[1] == "disabled":
            raise HTTPException(409, "Cannot promote a disabled user")

        await conn.execute(
            "UPDATE users SET role = 'admin' WHERE user_id = ?", (user_id,)
        )

        updated = await conn.fetchone(
            "SELECT user_id, role, status, created_at, last_active_at, disabled_at, disabled_by FROM users WHERE user_id = ?",
            (user_id,),
        )

    return {
        "success": True,
        "data": _row_to_user_dict(updated),
    }


@app.post("/api/admin/users/{user_id}/demote")
async def admin_demote_user(
    user_id: str,
    current_user: str = Depends(require_admin),
):
    if user_id == current_user:
        raise HTTPException(403, "Cannot demote your own account")

    async with _db.connection() as conn:
        row = await conn.fetchone(
            "SELECT role FROM users WHERE user_id = ?", (user_id,)
        )
        if row is None:
            raise HTTPException(404, "User not found")
        if row[0] != "admin":
            raise HTTPException(409, "User is not an admin")

        await conn.execute(
            "UPDATE users SET role = 'user' WHERE user_id = ?", (user_id,)
        )

        updated = await conn.fetchone(
            "SELECT user_id, role, status, created_at, last_active_at, disabled_at, disabled_by FROM users WHERE user_id = ?",
            (user_id,),
        )

    return {
        "success": True,
        "data": _row_to_user_dict(updated),
    }
```

- [ ] **Step 4: Run all tests**

```bash
uv run pytest tests/unit/test_user_management.py -v
```

Expected: all 18 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add main_server.py tests/unit/test_user_management.py
git commit -m "feat: add promote/demote user endpoints"
```

---

### Task 4: Frontend — i18n keys

**Files:**
- Modify: `frontend/src/i18n/en.json`
- Modify: `frontend/src/i18n/zh.json`

- [ ] **Step 1: Add en.json keys**

Insert after the `"header"` block in `frontend/src/i18n/en.json`:

```json
  "users": {
    "title": "User Management",
    "back": "← Back",
    "userList": "User List",
    "totalCount": "{{count}} users total",
    "searchPlaceholder": "Search User ID...",
    "allRoles": "All Roles",
    "allStatuses": "All Statuses",
    "search": "Search",
    "colUserId": "User ID",
    "colRole": "Role",
    "colStatus": "Status",
    "colTokens": "Tokens",
    "colSessions": "Sessions",
    "colRegistered": "Registered",
    "colLastActive": "Last Active",
    "colActions": "Actions",
    "roleAdmin": "Admin",
    "roleUser": "User",
    "statusActive": "Active",
    "statusDisabled": "Disabled",
    "actionPromote": "Promote",
    "actionDemote": "Demote",
    "actionDisable": "Disable",
    "actionEnable": "Enable",
    "currentUser": "Current User",
    "disabledBy": "Disabled by {{admin}} on {{date}}",
    "confirmDisableTitle": "Disable User",
    "confirmDisableBody": "Disabling <strong>{{user}}</strong> will prevent them from logging in. All data will be preserved.",
    "confirmDisableButton": "Disable",
    "confirmEnableTitle": "Enable User",
    "confirmEnableBody": "Enable <strong>{{user}}</strong>'s account? They will be able to log in again.",
    "confirmEnableButton": "Enable",
    "confirmPromoteTitle": "Promote to Admin",
    "confirmPromoteBody": "Promote <strong>{{user}}</strong> to admin? They will gain full admin privileges.",
    "confirmPromoteButton": "Promote",
    "confirmDemoteTitle": "Demote from Admin",
    "confirmDemoteBody": "Demote <strong>{{user}}</strong> from admin? They will lose all admin privileges.",
    "confirmDemoteButton": "Demote",
    "confirmCancel": "Cancel",
    "empty": "No users found",
    "loadError": "Failed to load users",
    "actionError": "Action failed"
  },
```

- [ ] **Step 2: Add zh.json keys**

Insert after the `"header"` block in `frontend/src/i18n/zh.json`:

```json
  "users": {
    "title": "用户管理",
    "back": "← 返回",
    "userList": "用户列表",
    "totalCount": "共 {{count}} 个用户",
    "searchPlaceholder": "搜索 User ID...",
    "allRoles": "全部角色",
    "allStatuses": "全部状态",
    "search": "搜索",
    "colUserId": "User ID",
    "colRole": "角色",
    "colStatus": "状态",
    "colTokens": "Token",
    "colSessions": "会话",
    "colRegistered": "注册时间",
    "colLastActive": "最后活跃",
    "colActions": "操作",
    "roleAdmin": "管理员",
    "roleUser": "用户",
    "statusActive": "活跃",
    "statusDisabled": "已禁用",
    "actionPromote": "提升",
    "actionDemote": "降级",
    "actionDisable": "禁用",
    "actionEnable": "启用",
    "currentUser": "当前用户",
    "disabledBy": "{{admin}} 于 {{date}} 禁用",
    "confirmDisableTitle": "禁用用户",
    "confirmDisableBody": "禁用 <strong>{{user}}</strong> 后将无法登录，所有数据保留。",
    "confirmDisableButton": "确认禁用",
    "confirmEnableTitle": "启用用户",
    "confirmEnableBody": "启用 <strong>{{user}}</strong> 的账户？他们将可以重新登录。",
    "confirmEnableButton": "确认启用",
    "confirmPromoteTitle": "提升为管理员",
    "confirmPromoteBody": "将 <strong>{{user}}</strong> 提升为管理员，获得全部管理权限。",
    "confirmPromoteButton": "确认提升",
    "confirmDemoteTitle": "降级管理员",
    "confirmDemoteBody": "将 <strong>{{user}}</strong> 从管理员降级？他们将失去所有管理权限。",
    "confirmDemoteButton": "确认降级",
    "confirmCancel": "取消",
    "empty": "未找到用户",
    "loadError": "加载用户失败",
    "actionError": "操作失败"
  },
```

- [ ] **Step 3: Verify JSON is valid**

```bash
cd frontend && node -e "JSON.parse(require('fs').readFileSync('src/i18n/en.json','utf8')); console.log('en.json OK')" && node -e "JSON.parse(require('fs').readFileSync('src/i18n/zh.json','utf8')); console.log('zh.json OK')"
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/i18n/en.json frontend/src/i18n/zh.json
git commit -m "feat: add users.* i18n keys for user management page"
```

---

### Task 5: Frontend — API hook

**Files:**
- Create: `frontend/src/hooks/useUsersApi.ts`

- [ ] **Step 1: Write the hook**

Create `frontend/src/hooks/useUsersApi.ts`:

```typescript
import { useState, useEffect, useCallback, useMemo } from 'react'

export interface UserItem {
  user_id: string
  role: 'admin' | 'user'
  status: 'active' | 'disabled'
  created_at: number
  last_active_at: number
  disabled_at: number | null
  disabled_by: string | null
  session_count: number
  total_tokens: number
}

export interface UsersListData {
  items: UserItem[]
  total: number
  page: number
  page_size: number
}

interface AsyncState<T> {
  data: T | null
  loading: boolean
  error: string | null
}

export interface UsersFilters {
  q: string
  role: string
  status: string
  sort: string
  order: string
}

const API_BASE = '/api/admin/users'

async function fetchJson<T>(url: string, token: string): Promise<T> {
  const headers: Record<string, string> = token
    ? { Authorization: `Bearer ${token}` }
    : {}
  const resp = await fetch(url, { headers })
  if (!resp.ok) {
    const detail = await resp
      .json()
      .then((b) => b.detail)
      .catch(() => resp.statusText)
    throw new Error(typeof detail === 'string' ? detail : resp.statusText)
  }
  return resp.json() as Promise<T>
}

async function postJson(url: string, token: string): Promise<UserItem> {
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  const resp = await fetch(url, { method: 'POST', headers })
  if (!resp.ok) {
    const detail = await resp
      .json()
      .then((b) => b.detail)
      .catch(() => resp.statusText)
    throw new Error(typeof detail === 'string' ? detail : resp.statusText)
  }
  const body = await resp.json()
  return body.data as UserItem
}

export function useUsersApi(filters: UsersFilters, page: number) {
  const authToken = useMemo(() => localStorage.getItem('authToken') || '', [])
  const [refreshKey, setRefreshKey] = useState(0)

  const [list, setList] = useState<AsyncState<UsersListData>>({
    data: null,
    loading: true,
    error: null,
  })

  const fetchList = useCallback(() => {
    setList((s) => ({ ...s, loading: true, error: null }))
    const params = new URLSearchParams()
    if (filters.q) params.set('q', filters.q)
    if (filters.role) params.set('role', filters.role)
    if (filters.status) params.set('status', filters.status)
    params.set('sort', filters.sort)
    params.set('order', filters.order)
    params.set('page', String(page))
    params.set('page_size', '20')

    fetchJson<{ success: boolean; data: UsersListData }>(
      `${API_BASE}?${params}`,
      authToken,
    )
      .then((res) => setList({ data: res.data, loading: false, error: null }))
      .catch((e: unknown) =>
        setList({
          data: null,
          loading: false,
          error: e instanceof Error ? e.message : 'Unknown error',
        }),
      )
  }, [authToken, filters.q, filters.role, filters.status, filters.sort, filters.order, page, refreshKey])

  useEffect(() => {
    fetchList()
  }, [fetchList])

  const disableUser = useCallback(
    (userId: string): Promise<UserItem> =>
      postJson(`${API_BASE}/${encodeURIComponent(userId)}/disable`, authToken),
    [authToken],
  )

  const enableUser = useCallback(
    (userId: string): Promise<UserItem> =>
      postJson(`${API_BASE}/${encodeURIComponent(userId)}/enable`, authToken),
    [authToken],
  )

  const promoteUser = useCallback(
    (userId: string): Promise<UserItem> =>
      postJson(`${API_BASE}/${encodeURIComponent(userId)}/promote`, authToken),
    [authToken],
  )

  const demoteUser = useCallback(
    (userId: string): Promise<UserItem> =>
      postJson(`${API_BASE}/${encodeURIComponent(userId)}/demote`, authToken),
    [authToken],
  )

  const refetch = useCallback(() => {
    setRefreshKey((k) => k + 1)
  }, [])

  return { list, disableUser, enableUser, promoteUser, demoteUser, refetch }
}
```

- [ ] **Step 2: Type check**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useUsersApi.ts
git commit -m "feat: add useUsersApi hook for user management"
```

---

### Task 6: Frontend — Page & sub-components

**Files:**
- Create: `frontend/src/pages/UsersPage.tsx`
- Create: `frontend/src/pages/users/UsersFilter.tsx`
- Create: `frontend/src/pages/users/UsersTable.tsx`
- Create: `frontend/src/pages/users/ConfirmDialog.tsx`

- [ ] **Step 1: Create ConfirmDialog**

Create `frontend/src/pages/users/ConfirmDialog.tsx`:

```typescript
import { useTranslation } from 'react-i18next'

interface ConfirmDialogProps {
  title: string
  body: string
  confirmLabel: string
  confirmClass: 'danger' | 'primary'
  loading: boolean
  onConfirm: () => void
  onCancel: () => void
}

export default function ConfirmDialog({
  title,
  body,
  confirmLabel,
  confirmClass,
  loading,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const { t } = useTranslation()

  return (
    <div
      style={{
        background: 'var(--color-surface, #fff)',
        border: '1px solid var(--color-border, #e2e8f0)',
        borderRadius: '12px',
        padding: '20px',
        marginTop: '20px',
      }}
    >
      <h3
        style={{
          fontSize: '0.9375rem',
          fontWeight: 600,
          color: 'var(--color-text, #1a202c)',
          margin: '0 0 16px 0',
        }}
      >
        {title}
      </h3>
      <div
        style={{
          padding: '16px',
          background: 'var(--color-surface, #fff)',
          border:
            confirmClass === 'danger'
              ? '1px solid #fee2e2'
              : '1px solid var(--color-border, #e2e8f0)',
          borderRadius: '8px',
        }}
      >
        <p
          style={{ fontSize: '13px', color: 'var(--color-text-muted, #718096)', marginBottom: '12px' }}
          dangerouslySetInnerHTML={{ __html: body }}
        />
        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
          <button
            onClick={onCancel}
            disabled={loading}
            style={{
              padding: '6px 14px',
              border: '1px solid var(--color-border, #e2e8f0)',
              borderRadius: '6px',
              background: 'var(--color-surface, #fff)',
              fontSize: '13px',
              cursor: 'pointer',
              color: 'var(--color-text-muted, #64748b)',
            }}
          >
            {t('users.confirmCancel')}
          </button>
          <button
            onClick={onConfirm}
            disabled={loading}
            style={{
              padding: '6px 14px',
              border: 'none',
              borderRadius: '6px',
              background: confirmClass === 'danger' ? '#dc2626' : '#3b82f6',
              color: '#fff',
              fontSize: '13px',
              cursor: 'pointer',
              opacity: loading ? 0.7 : 1,
            }}
          >
            {loading ? '...' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Create UsersFilter**

Create `frontend/src/pages/users/UsersFilter.tsx`:

```typescript
import { useTranslation } from 'react-i18next'

interface UsersFilterProps {
  q: string
  role: string
  status: string
  totalCount: number
  onQChange: (q: string) => void
  onRoleChange: (role: string) => void
  onStatusChange: (status: string) => void
  onSearch: () => void
}

export default function UsersFilter({
  q,
  role,
  status,
  totalCount,
  onQChange,
  onRoleChange,
  onStatusChange,
  onSearch,
}: UsersFilterProps) {
  const { t } = useTranslation()

  return (
    <div className="ranking-panel">
      <div
        style={{
          display: 'flex',
          gap: '10px',
          alignItems: 'center',
          flexWrap: 'wrap',
        }}
      >
        <input
          type="text"
          placeholder={t('users.searchPlaceholder')}
          value={q}
          onChange={(e) => onQChange(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onSearch()}
          style={{
            padding: '7px 12px',
            border: '1px solid var(--color-border, #e2e8f0)',
            borderRadius: '6px',
            fontSize: '13px',
            width: '200px',
            background: 'var(--color-surface, #fff)',
            color: 'var(--color-text, #1a202c)',
          }}
        />
        <select
          value={role}
          onChange={(e) => onRoleChange(e.target.value)}
          style={{
            padding: '7px 12px',
            border: '1px solid var(--color-border, #e2e8f0)',
            borderRadius: '6px',
            fontSize: '13px',
            background: 'var(--color-surface, #fff)',
            color: 'var(--color-text, #1a202c)',
          }}
        >
          <option value="">{t('users.allRoles')}</option>
          <option value="admin">{t('users.roleAdmin')}</option>
          <option value="user">{t('users.roleUser')}</option>
        </select>
        <select
          value={status}
          onChange={(e) => onStatusChange(e.target.value)}
          style={{
            padding: '7px 12px',
            border: '1px solid var(--color-border, #e2e8f0)',
            borderRadius: '6px',
            fontSize: '13px',
            background: 'var(--color-surface, #fff)',
            color: 'var(--color-text, #1a202c)',
          }}
        >
          <option value="">{t('users.allStatuses')}</option>
          <option value="active">{t('users.statusActive')}</option>
          <option value="disabled">{t('users.statusDisabled')}</option>
        </select>
        <button
          onClick={onSearch}
          style={{
            padding: '7px 18px',
            border: '1px solid #3b82f6',
            borderRadius: '6px',
            background: '#eff6ff',
            color: '#3b82f6',
            fontSize: '13px',
            cursor: 'pointer',
            fontWeight: 500,
          }}
        >
          {t('users.search')}
        </button>
        <span
          style={{
            marginLeft: 'auto',
            fontSize: '12px',
            color: 'var(--color-text-muted, #718096)',
          }}
        >
          {t('users.totalCount', { count: totalCount })}
        </span>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Create UsersTable**

Create `frontend/src/pages/users/UsersTable.tsx`:

```typescript
import { useTranslation } from 'react-i18next'
import type { UserItem } from '../../hooks/useUsersApi'

function formatTokens(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K'
  return String(n)
}

function formatDate(ts: number | null): string {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function formatShortDate(ts: number | null): string {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

interface UsersTableProps {
  items: UserItem[]
  currentUserId: string
  loading: boolean
  onDisable: (userId: string) => void
  onEnable: (userId: string) => void
  onPromote: (userId: string) => void
  onDemote: (userId: string) => void
}

export default function UsersTable({
  items,
  currentUserId,
  loading,
  onDisable,
  onEnable,
  onPromote,
  onDemote,
}: UsersTableProps) {
  const { t } = useTranslation()

  return (
    <div className="ranking-panel" style={{ marginTop: '20px' }}>
      <h3 className="ranking-title">{t('users.userList')}</h3>
      <table className="ranking-table">
        <thead>
          <tr>
            <th>{t('users.colUserId')}</th>
            <th>{t('users.colRole')}</th>
            <th>{t('users.colStatus')}</th>
            <th className="right">{t('users.colTokens')}</th>
            <th className="right">{t('users.colSessions')}</th>
            <th>{t('users.colRegistered')}</th>
            <th>{t('users.colLastActive')}</th>
            <th style={{ textAlign: 'center' }}>{t('users.colActions')}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((u) => {
            const isCurrentUser = u.user_id === currentUserId
            const isDisabled = u.status === 'disabled'
            return (
              <tr
                key={u.user_id}
                style={isDisabled ? { background: '#fef2f2' } : undefined}
              >
                <td style={{ fontWeight: 500 }}>{u.user_id}</td>
                <td>
                  <span
                    style={{
                      background: u.role === 'admin' ? '#dbeafe' : '#f1f5f9',
                      color: u.role === 'admin' ? '#3b82f6' : '#64748b',
                      padding: '1px 8px',
                      borderRadius: '4px',
                      fontSize: '12px',
                    }}
                  >
                    {u.role === 'admin' ? t('users.roleAdmin') : t('users.roleUser')}
                  </span>
                </td>
                <td>
                  <span
                    style={{
                      background: isDisabled ? '#fee2e2' : '#dcfce7',
                      color: isDisabled ? '#dc2626' : '#15803d',
                      padding: '1px 8px',
                      borderRadius: '4px',
                      fontSize: '12px',
                    }}
                  >
                    {isDisabled ? t('users.statusDisabled') : t('users.statusActive')}
                  </span>
                </td>
                <td className="right mono">{formatTokens(u.total_tokens)}</td>
                <td className="right">{u.session_count}</td>
                <td style={{ color: 'var(--color-text-muted, #718096)' }}>
                  {formatShortDate(u.created_at)}
                </td>
                <td style={{ color: 'var(--color-text-muted, #718096)' }}>
                  {formatDate(u.last_active_at)}
                </td>
                <td style={{ textAlign: 'center', whiteSpace: 'nowrap' }}>
                  {isCurrentUser ? (
                    <span style={{ fontSize: '12px', color: 'var(--color-text-muted, #718096)' }}>
                      {t('users.currentUser')}
                    </span>
                  ) : isDisabled ? (
                    <>
                      {u.disabled_by && u.disabled_at ? (
                        <span
                          style={{
                            fontSize: '11px',
                            color: 'var(--color-text-muted, #718096)',
                            display: 'block',
                          }}
                        >
                          {t('users.disabledBy', {
                            admin: u.disabled_by,
                            date: formatShortDate(u.disabled_at),
                          })}
                        </span>
                      ) : null}
                      <button
                        onClick={() => onEnable(u.user_id)}
                        disabled={loading}
                        style={{
                          padding: '4px 10px',
                          border: '1px solid var(--color-border, #e2e8f0)',
                          borderRadius: '4px',
                          background: 'var(--color-surface, #fff)',
                          fontSize: '12px',
                          cursor: 'pointer',
                          color: '#15803d',
                          marginTop: '3px',
                        }}
                      >
                        {t('users.actionEnable')}
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={() =>
                          u.role === 'admin' ? onDemote(u.user_id) : onPromote(u.user_id)
                        }
                        disabled={loading}
                        style={{
                          padding: '4px 10px',
                          border: '1px solid var(--color-border, #e2e8f0)',
                          borderRadius: '4px',
                          background: 'var(--color-surface, #fff)',
                          fontSize: '12px',
                          cursor: 'pointer',
                          color: '#3b82f6',
                          marginRight: '4px',
                        }}
                      >
                        {u.role === 'admin' ? t('users.actionDemote') : t('users.actionPromote')}
                      </button>
                      <button
                        onClick={() => onDisable(u.user_id)}
                        disabled={loading}
                        style={{
                          padding: '4px 10px',
                          border: '1px solid var(--color-border, #e2e8f0)',
                          borderRadius: '4px',
                          background: 'var(--color-surface, #fff)',
                          fontSize: '12px',
                          cursor: 'pointer',
                          color: '#b45309',
                        }}
                      >
                        {t('users.actionDisable')}
                      </button>
                    </>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {!loading && items.length === 0 && (
        <div style={{ padding: '20px', textAlign: 'center', color: 'var(--color-text-muted, #718096)' }}>
          {t('users.empty')}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Create UsersPage**

Create `frontend/src/pages/UsersPage.tsx`:

```typescript
import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useUsersApi, type UsersFilters } from '../hooks/useUsersApi'
import UsersFilter from './users/UsersFilter'
import UsersTable from './users/UsersTable'
import ConfirmDialog from './users/ConfirmDialog'

type DialogType = 'disable' | 'enable' | 'promote' | 'demote' | null

interface PendingAction {
  type: DialogType
  userId: string
}

export default function UsersPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  const [filters, setFilters] = useState<UsersFilters>({
    q: '',
    role: '',
    status: '',
    sort: 'created_at',
    order: 'desc',
  })
  const [page, setPage] = useState(1)
  const [pending, setPending] = useState<PendingAction | null>(null)
  const [actionLoading, setActionLoading] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const api = useUsersApi(filters, page)
  const currentUserId = localStorage.getItem('userId') || ''

  const totalPages = api.list.data
    ? Math.ceil(api.list.data.total / api.list.data.page_size)
    : 0

  const confirmAction = useCallback(async () => {
    if (!pending) return
    setActionLoading(true)
    setActionError(null)
    try {
      const { type, userId } = pending
      if (type === 'disable') await api.disableUser(userId)
      else if (type === 'enable') await api.enableUser(userId)
      else if (type === 'promote') await api.promoteUser(userId)
      else if (type === 'demote') await api.demoteUser(userId)
      api.refetch()
      setPending(null)
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : t('users.actionError'))
    } finally {
      setActionLoading(false)
    }
  }, [pending, api, t])

  const cancelAction = useCallback(() => {
    setPending(null)
    setActionError(null)
  }, [])

  const triggerAction = useCallback((type: DialogType, userId: string) => {
    setPending({ type, userId })
    setActionError(null)
  }, [])

  const dialogTitle =
    pending?.type === 'disable'
      ? t('users.confirmDisableTitle')
      : pending?.type === 'enable'
        ? t('users.confirmEnableTitle')
        : pending?.type === 'promote'
          ? t('users.confirmPromoteTitle')
          : pending?.type === 'demote'
            ? t('users.confirmDemoteTitle')
            : ''

  const dialogBody =
    pending?.type === 'disable'
      ? t('users.confirmDisableBody', { user: pending.userId })
      : pending?.type === 'enable'
        ? t('users.confirmEnableBody', { user: pending.userId })
        : pending?.type === 'promote'
          ? t('users.confirmPromoteBody', { user: pending.userId })
          : pending?.type === 'demote'
            ? t('users.confirmDemoteBody', { user: pending.userId })
            : ''

  const dialogConfirmLabel =
    pending?.type === 'disable'
      ? t('users.confirmDisableButton')
      : pending?.type === 'enable'
        ? t('users.confirmEnableButton')
        : pending?.type === 'promote'
          ? t('users.confirmPromoteButton')
          : t('users.confirmDemoteButton')

  const dialogConfirmClass =
    pending?.type === 'disable' || pending?.type === 'demote' ? 'danger' : 'primary'

  return (
    <div className="detail-page">
      <div className="detail-header">
        <button className="detail-back-btn" onClick={() => navigate('/')}>
          {t('users.back')}
        </button>
        <h2
          style={{
            fontSize: '1.25rem',
            fontWeight: 600,
            color: 'var(--color-text, #1a202c)',
            margin: 0,
          }}
        >
          {t('users.title')}
        </h2>
      </div>

      {api.list.error && (
        <div
          style={{
            padding: '12px 16px',
            background: '#fef2f2',
            border: '1px solid #fee2e2',
            borderRadius: '6px',
            color: '#dc2626',
            fontSize: '13px',
          }}
        >
          {api.list.error}
          <button
            onClick={api.refetch}
            style={{
              marginLeft: '12px',
              padding: '2px 10px',
              border: '1px solid #dc2626',
              borderRadius: '4px',
              background: '#fff',
              color: '#dc2626',
              cursor: 'pointer',
              fontSize: '12px',
            }}
          >
            {t('common.retry')}
          </button>
        </div>
      )}

      <UsersFilter
        q={filters.q}
        role={filters.role}
        status={filters.status}
        totalCount={api.list.data?.total ?? 0}
        onQChange={(q) => setFilters((f) => ({ ...f, q }))}
        onRoleChange={(role) => {
          setFilters((f) => ({ ...f, role }))
          setPage(1)
        }}
        onStatusChange={(status) => {
          setFilters((f) => ({ ...f, status }))
          setPage(1)
        }}
        onSearch={() => {
          setPage(1)
          api.refetch()
        }}
      />

      {api.list.loading && (
        <div
          style={{
            padding: '40px',
            textAlign: 'center',
            color: 'var(--color-text-muted, #718096)',
          }}
        >
          {t('common.loading')}
        </div>
      )}

      {!api.list.loading && (
        <UsersTable
          items={api.list.data?.items ?? []}
          currentUserId={currentUserId}
          loading={actionLoading}
          onDisable={(uid) => triggerAction('disable', uid)}
          onEnable={(uid) => triggerAction('enable', uid)}
          onPromote={(uid) => triggerAction('promote', uid)}
          onDemote={(uid) => triggerAction('demote', uid)}
        />
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            gap: '4px',
            marginTop: '16px',
          }}
        >
          <button
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            style={{
              padding: '5px 10px',
              border: '1px solid var(--color-border, #e2e8f0)',
              borderRadius: '6px',
              background: 'var(--color-surface, #fff)',
              fontSize: '12px',
              color: 'var(--color-text-muted, #64748b)',
              cursor: page <= 1 ? 'default' : 'pointer',
              opacity: page <= 1 ? 0.5 : 1,
            }}
          >
            ‹
          </button>
          {Array.from({ length: Math.min(totalPages, 5) }, (_, i) => {
            const pageNum = i + 1
            return (
              <button
                key={pageNum}
                onClick={() => setPage(pageNum)}
                style={{
                  padding: '5px 10px',
                  background: pageNum === page ? '#eff6ff' : 'transparent',
                  color: pageNum === page ? '#3b82f6' : 'var(--color-text-muted, #64748b)',
                  borderRadius: '6px',
                  fontSize: '12px',
                  fontWeight: pageNum === page ? 600 : 400,
                  border: 'none',
                  cursor: 'pointer',
                }}
              >
                {pageNum}
              </button>
            )
          })}
          <button
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            style={{
              padding: '5px 10px',
              border: '1px solid var(--color-border, #e2e8f0)',
              borderRadius: '6px',
              background: 'var(--color-surface, #fff)',
              fontSize: '12px',
              color: 'var(--color-text-muted, #64748b)',
              cursor: page >= totalPages ? 'default' : 'pointer',
              opacity: page >= totalPages ? 0.5 : 1,
            }}
          >
            ›
          </button>
        </div>
      )}

      {pending && (
        <ConfirmDialog
          title={dialogTitle}
          body={dialogBody}
          confirmLabel={dialogConfirmLabel}
          confirmClass={dialogConfirmClass}
          loading={actionLoading}
          onConfirm={confirmAction}
          onCancel={cancelAction}
        />
      )}

      {actionError && (
        <div
          style={{
            padding: '10px 16px',
            background: '#fef2f2',
            border: '1px solid #fee2e2',
            borderRadius: '6px',
            color: '#dc2626',
            fontSize: '13px',
            marginTop: '12px',
          }}
        >
          {actionError}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 5: Run type check**

```bash
cd frontend && npx tsc --noEmit
```

Fix any type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/UsersPage.tsx frontend/src/pages/users/
git commit -m "feat: add UsersPage with filter, table, confirm dialog, pagination"
```

---

### Task 7: Frontend — Wire up route and navigation

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Header.tsx`
- Modify: `frontend/src/components/SettingsMenu.tsx`

- [ ] **Step 1: Add route in App.tsx**

After the `/evolution` route block (line ~1589), add:

```tsx
      <Route
        path="/users"
        element={
          userRole === "admin" ? (
            <UsersPage />
          ) : (
            <Navigate to="/" replace />
          )
        }
      />
```

Also add the import at the top of `App.tsx`:

```typescript
import UsersPage from "./pages/UsersPage";
```

- [ ] **Step 2: Add onOpenUsers prop to Header**

In `Header.tsx`, add `onOpenUsers: () => void` to the `HeaderProps` interface and destructure it. Then pass it to `SettingsMenu`:

In `HeaderProps` (after `onOpenDashboard`):
```typescript
  onOpenUsers: () => void
```

In the destructuring (after `onOpenDashboard`):
```typescript
  onOpenUsers,
```

In the `SettingsMenu` JSX (after `onOpenDashboard`):
```tsx
          onOpenUsers={onOpenUsers}
```

- [ ] **Step 3: Add onOpenUsers prop to SettingsMenu**

In `SettingsMenu.tsx`, add `onOpenUsers: () => void` to `SettingsMenuProps`, destructure it, and add a menu item:

In the interface:
```typescript
  onOpenUsers: () => void
```

In the destructuring:
```typescript
export default function SettingsMenu({ onOpenSkills, onOpenEvolution, onOpenMCP, onOpenDashboard, onOpenUsers, userRole }: SettingsMenuProps) {
```

Add menu item inside the `{isAdmin && (` block (e.g., after the dashboard item):

```tsx
          {isAdmin && (
            <button className="settings-menu-item" role="menuitem" onClick={() => handleAction(onOpenUsers)} type="button">
              <span className="settings-menu-item-icon">👥</span>
              {t('users.title')}
            </button>
          )}
```

- [ ] **Step 4: Wire callback in App.tsx Header usage**

In the `<Header ...>` JSX (around line 274), add:

```tsx
        onOpenUsers={() => navigate("/users")}
```

- [ ] **Step 5: Run type check**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/Header.tsx frontend/src/components/SettingsMenu.tsx
git commit -m "feat: wire /users route and settings menu navigation"
```

---

### Task 8: Frontend — Unit tests

**Files:**
- Create: `frontend/src/pages/users/UsersTable.test.tsx`

- [ ] **Step 1: Write unit test for UsersTable**

Create `frontend/src/pages/users/UsersTable.test.tsx`:

```typescript
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import UsersTable from './UsersTable'
import type { UserItem } from '../../hooks/useUsersApi'

// Mock react-i18next
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      const map: Record<string, string> = {
        'users.userList': 'User List',
        'users.colUserId': 'User ID',
        'users.colRole': 'Role',
        'users.colStatus': 'Status',
        'users.colTokens': 'Tokens',
        'users.colSessions': 'Sessions',
        'users.colRegistered': 'Registered',
        'users.colLastActive': 'Last Active',
        'users.colActions': 'Actions',
        'users.roleAdmin': 'Admin',
        'users.roleUser': 'User',
        'users.statusActive': 'Active',
        'users.statusDisabled': 'Disabled',
        'users.currentUser': 'Current User',
        'users.actionPromote': 'Promote',
        'users.actionDemote': 'Demote',
        'users.actionDisable': 'Disable',
        'users.actionEnable': 'Enable',
        'users.empty': 'No users found',
        'users.disabledBy': `Disabled by ${opts?.admin} on ${opts?.date}`,
      }
      return map[key] ?? key
    },
    i18n: { changeLanguage: vi.fn() },
  }),
}))

const mockUsers: UserItem[] = [
  {
    user_id: 'admin',
    role: 'admin',
    status: 'active',
    created_at: 1713168000,
    last_active_at: 1715432150,
    disabled_at: null,
    disabled_by: null,
    session_count: 143,
    total_tokens: 2460000,
  },
  {
    user_id: 'zhangsan',
    role: 'user',
    status: 'active',
    created_at: 1713500000,
    last_active_at: 1715400000,
    disabled_at: null,
    disabled_by: null,
    session_count: 67,
    total_tokens: 856100,
  },
  {
    user_id: 'wangwu',
    role: 'user',
    status: 'disabled',
    created_at: 1713600000,
    last_active_at: 1715000000,
    disabled_at: 1715000000,
    disabled_by: 'admin',
    session_count: 3,
    total_tokens: 12400,
  },
]

describe('UsersTable', () => {
  const noop = vi.fn()

  it('renders column headers', () => {
    render(
      <UsersTable
        items={mockUsers}
        currentUserId="admin"
        loading={false}
        onDisable={noop}
        onEnable={noop}
        onPromote={noop}
        onDemote={noop}
      />,
    )
    expect(screen.getByText('User ID')).toBeTruthy()
    expect(screen.getByText('Role')).toBeTruthy()
    expect(screen.getByText('Status')).toBeTruthy()
    expect(screen.getByText('Actions')).toBeTruthy()
  })

  it('renders all user rows', () => {
    render(
      <UsersTable
        items={mockUsers}
        currentUserId="admin"
        loading={false}
        onDisable={noop}
        onEnable={noop}
        onPromote={noop}
        onDemote={noop}
      />,
    )
    expect(screen.getByText('admin')).toBeTruthy()
    expect(screen.getByText('zhangsan')).toBeTruthy()
    expect(screen.getByText('wangwu')).toBeTruthy()
  })

  it('shows current user label for the current user', () => {
    render(
      <UsersTable
        items={mockUsers}
        currentUserId="admin"
        loading={false}
        onDisable={noop}
        onEnable={noop}
        onPromote={noop}
        onDemote={noop}
      />,
    )
    expect(screen.getByText('Current User')).toBeTruthy()
  })

  it('shows enable button for disabled users', () => {
    render(
      <UsersTable
        items={mockUsers}
        currentUserId="admin"
        loading={false}
        onDisable={noop}
        onEnable={noop}
        onPromote={noop}
        onDemote={noop}
      />,
    )
    expect(screen.getByText('Enable')).toBeTruthy()
  })

  it('shows empty state when no items', () => {
    render(
      <UsersTable
        items={[]}
        currentUserId="admin"
        loading={false}
        onDisable={noop}
        onEnable={noop}
        onPromote={noop}
        onDemote={noop}
      />,
    )
    expect(screen.getByText('No users found')).toBeTruthy()
  })

  it('shows promote/demote buttons for active non-current users', () => {
    render(
      <UsersTable
        items={mockUsers}
        currentUserId="admin"
        loading={false}
        onDisable={noop}
        onEnable={noop}
        onPromote={noop}
        onDemote={noop}
      />,
    )
    expect(screen.getByText('Promote')).toBeTruthy()
    expect(screen.getByText('Disable')).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run tests**

```bash
cd frontend && npx vitest run src/pages/users/UsersTable.test.tsx
```

Expected: all 6 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/users/UsersTable.test.tsx
git commit -m "test: add UsersTable unit tests"
```

---

### Task 9: Verification

- [ ] **Step 1: Run full backend test suite**

```bash
uv run pytest tests/ -v
```

Expected: all tests pass, including the new `test_user_management.py`.

- [ ] **Step 2: Run frontend type check + tests**

```bash
cd frontend && npx tsc --noEmit && npx vitest run
```

Expected: zero type errors, all tests pass.

- [ ] **Step 3: Manual curl verification (optional)**

```bash
# List users
curl -s http://localhost:8000/api/admin/users | python3 -m json.tool | head -20

# Disable a test user
curl -s -X POST http://localhost:8000/api/admin/users/testuser/disable | python3 -m json.tool

# Enable again
curl -s -X POST http://localhost:8000/api/admin/users/testuser/enable | python3 -m json.tool
```

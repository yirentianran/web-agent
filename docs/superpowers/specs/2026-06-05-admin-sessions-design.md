# Admin Sessions Management — Design Spec

**Goal:** 管理员查看所有用户的 session 列表，支持筛选、排序、聚合统计、内容预览和批量操作。

## Backend

### New Endpoint: `GET /api/admin/sessions`

Query params: `q`, `user_id`, `status`, `from_date`, `to_date`, `sort`, `order`, `page` (default 1), `page_size` (default 20).

Sort columns: `created_at`, `last_active_at`, `message_count`, `total_tokens`.

Response:
```json
{
  "success": true,
  "data": {
    "items": [{
      "session_id": "sess_a1b2c3d4e5f6",
      "user_id": "alice",
      "title": "帮我分析财务报表",
      "status": "running",
      "message_count": 47,
      "total_tokens": 15234,
      "created_at": 1749198203,
      "last_active_at": 1749199500
    }],
    "total": 256,
    "page": 1,
    "page_size": 20
  }
}
```

SQL: JOIN `sessions` with `messages` aggregate (`SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens)`) for token count.

### New Endpoint: `GET /api/admin/sessions/aggregate`

Query params: `from_date`, `to_date`.

Returns stats cards + by-user + by-date aggregations:
```json
{
  "overview": { "total_sessions": 256, "active_sessions": 12, "total_users": 48, "total_tokens": 450000 },
  "by_user": [{ "user_id": "alice", "session_count": 68, "message_count": 2341, "total_tokens": 82300 }],
  "by_date": [{ "date": "2026-06-05", "session_count": 18, "message_count": 623, "total_tokens": 21500 }]
}
```

### Modified Endpoint: `GET /api/admin/sessions/{session_id}/messages`

Already exists (line 6383). Add `page` + `page_size` params for pagination.

### SessionStore: `list_all_sessions()` method

Wraps the admin query — no user_id filter. Optional JOIN with messages for token aggregation.

## Frontend

### New Page: `SessionsPage.tsx`

Path: `frontend/src/pages/SessionsPage.tsx`

Follows UsersPage pattern:
- `useSessionsApi` hook — `fetchList(filters, page)`, `cancelSession(id)`, `deleteSession(id)`
- `SessionsFilter` — user dropdown, status dropdown, date range, sort
- `SessionsTable` — checkbox, session_id, user, title, status badge, msgs, tokens, created, actions
- Expandable preview row — latest 10 messages (horizontal scroll cards)
- Batch action bar (visible when ≥2 selected)
- Chat overlay (click 📋 opens full conversation via existing ChatArea pattern)
- Pagination

### Route: `App.tsx`

```tsx
<Route path="/sessions" element={
  roleLoading ? null : userRole === "admin" ? <SessionsPage /> : <Navigate to="/" />
}/>
```

### Navigation: `SettingsMenu.tsx` + `Header.tsx` + `App.tsx`

Add "Sessions" menu item (📋 icon) for admin users, navigates to `/sessions`.

## Data Flow

```
SessionsPage
  → useSessionsApi.fetchList(filters, page)
    → GET /api/admin/sessions?user_id=...&status=...&page=1
    → SessionStore.list_all_sessions()
  → useSessionsApi.fetchAggregate(from, to)
    → GET /api/admin/sessions/aggregate?from_date=...&to_date=...
  → onClick row → expand preview row (inline, latest 10 msgs)
  → onClick 📋 → open chat overlay (reuses ChatArea-like view)
  → select checkboxes → batch bar → batch cancel/delete
```

## Verification

1. Backend: `uv run pytest tests/unit/test_session_store.py -v` — verify `list_all_sessions` query
2. Integration: `uv run pytest tests/integration/test_admin_sessions.py -v`
3. Frontend: `npx tsc --noEmit`, `npx vitest run src/pages/SessionsPage.test.tsx`
4. Manual: login as admin → SettingsMenu → Sessions → verify table, filters, expand, batch ops

# User Management — Design Spec

**Date**: 2026-05-24
**Status**: draft
**Route**: `/users` (admin-only standalone page)

## Overview

Admin-only user management page. List all registered users with search, filter, sort, and pagination. Admins can disable/enable accounts and promote/demote admin role. No user deletion.

## Backend API

All endpoints under `/api/admin/users`, protected by `require_admin` dependency.

### `GET /api/admin/users`

List users with pagination, search, filter, sort.

**Query params**:
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `q` | string | `""` | Search by user_id (LIKE) |
| `role` | string | `""` | Filter: `"admin"` / `"user"` / `""`=all |
| `status` | string | `""` | Filter: `"active"` / `"disabled"` / `""`=all |
| `sort` | string | `"created_at"` | Column to sort by |
| `order` | string | `"desc"` | `"asc"` / `"desc"` |
| `page` | int | `1` | Page number |
| `page_size` | int | `20` | Items per page |

**Allowed sort columns**: `user_id`, `role`, `status`, `created_at`, `last_active_at`

**Response**:
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "user_id": "zhangsan",
        "role": "user",
        "status": "active",
        "created_at": 1713168000.0,
        "last_active_at": 1715432150.0,
        "disabled_at": null,
        "disabled_by": null,
        "session_count": 67,
        "total_tokens": 876646
      }
    ],
    "total": 1248,
    "page": 1,
    "page_size": 20
  }
}
```

SQL: JOIN with aggregated sessions and messages data for stats columns. Sort/filter added dynamically (parameterized for values, whitelisted for column names).

### `POST /api/admin/users/{user_id}/disable`

Set `status = 'disabled'`, `disabled_at = now`, `disabled_by = current_user`. Returns updated user. Cannot disable self. Cannot disable an already-disabled user (409).

### `POST /api/admin/users/{user_id}/enable`

Set `status = 'active'`, clear `disabled_at` and `disabled_by`. Cannot enable an already-active user (409).

### `POST /api/admin/users/{user_id}/promote`

Set `role = 'admin'`. Cannot promote an already-admin user (409). Cannot promote a disabled user (409).

### `POST /api/admin/users/{user_id}/demote`

Set `role = 'user'`. Cannot demote a non-admin user (409). Cannot demote self.

## Frontend

### Files

```
frontend/src/
├── pages/UsersPage.tsx          # Page component
├── pages/users/
│   ├── UsersTable.tsx           # User list table
│   ├── UsersFilter.tsx          # Search/filter bar
│   └── ConfirmDialog.tsx        # Action confirmation dialog
├── hooks/useUsersApi.ts         # API hook following useEvolutionApi pattern
└── i18n/en.json + zh.json      # Translation keys
```

### Route

In `App.tsx`, add route guarded by `userRole === "admin"`:

```tsx
<Route path="/users" element={
  userRole === "admin" ? <UsersPage /> : <Navigate to="/" replace />
} />
```

Add navigation entry in `SettingsMenu.tsx` under admin section.

### Page Structure

CSS: reuse `.detail-page`, `.detail-header`, `.detail-back-btn`, `.ranking-panel`, `.ranking-table` from existing stylesheet.

```
.detail-page
  .detail-header
    ← Back button (navigate to /chat)
    "用户管理" title
  .ranking-panel (search/filter)
    input (User ID search)
    select (role filter)
    select (status filter)
    Search button
    Total count (right-aligned)
  .ranking-panel (table)
    h3: "用户列表"
    table.ranking-table
      thead: User ID | Role | Status | Token | Sessions | Registered | Last Active | Actions
      tbody: rows with role/status badges
    Pagination controls
  ConfirmDialog (conditional)
```

### States

| State | Behavior |
|-------|----------|
| Loading | Table skeleton or spinner |
| Empty | "No users found" message |
| Error | Error banner with retry button |
| Action pending | Button shows loading, disabled until done |
| Action error | Error toast, rollback to previous state |
| Success | Toast notification, row updates in place |

### i18n Keys

Namespaced under `users.*`:
- `users.title`, `users.back`, `users.search`, `users.filterByRole`, `users.filterByStatus`
- `users.col*.` for column headers, `users.role.*`, `users.status.*`
- `users.actions.*`, `users.confirm.*`, `users.toast.*`

## Verification

1. **Backend**: `uv run pytest tests/ -k "user" -v` — unit tests for user endpoints
2. **Frontend**: `cd frontend && npx tsc --noEmit && npm test` — type check + Vitest
3. **Integration**: curl test each endpoint with admin token, verify 403 for non-admin
4. **UI**: Load `/users` page, test search/filter/pagination, test disable→enable cycle, test promote→demote cycle, verify self-disable blocked

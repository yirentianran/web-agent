# Skill Database Management Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add database-backed skill registry, usage tracking, and version metadata to replace file-system scanning and enable search, filtering, and analytics.

**Architecture:** Three new SQLite tables (`skills`, `skill_usage`, `skill_versions`) that layer on top of a restructured file-based storage. File system remains the source of truth for content; DB provides indexing, metadata, and analytics.

**Version Storage:** Historical versions live in **flat sibling directories** (`{skill_name}@vN/`) rather than nested subdirectories. This keeps downloads and agent loading clean: only the active version sits in the main skill directory.

```
data/shared-skills/
├── code-review/          ← active version (only SKILL.md + resources)
│   ├── SKILL.md
│   └── references/
├── code-review@v1/       ← historical v1 (isolated)
│   └── SKILL.md
├── code-review@v2/       ← historical v2 (isolated)
│   └── SKILL.md
└── ...
```

Same structure for personal skills under `data/users/{user_id}/workspace/.claude/skills/`.

**Tech Stack:** SQLite (aiosqlite), FastAPI, existing `src/database.py` schema.

---

## Current State

Skills are stored as directories on the filesystem:
- **Shared skills:** `data/shared-skills/{skill_name}/`
- **Personal skills:** `data/users/{user_id}/workspace/.claude/skills/{skill_name}/`
- **Metadata:** `skill-meta.json` within each skill directory (may be missing for older skills)
- **Versions:** File-based (`SKILL_v*.md`, `versions/vN/`, or `{skill_name}@vN/`) managed by `src/skill_feedback.py`
- **Feedback:** `skill_feedback` table in SQLite

Current pain points:
1. All listing/searching requires filesystem scanning
2. No usage tracking — can't tell which skills are actually used
3. Version metadata (active version, change history) only in files, not queryable

### Version Isolation (New)

Historical versions are stored as **flat sibling directories** using `{skill_name}@vN/` naming, not nested inside the active version. Benefits:
- Agent's `load_skills()` only scans root-level directories, never reads old versions
- Download ZIP only includes active version, not historical baggage
- Activating/switching versions is simple directory rename/replace
- `load_skills()` skips `@vN` directories via name pattern filter

## Architecture

### Data Flow

```
Startup Migration:  Filesystem scan → skills table (one-time per skill)
Skill Create/Upload: File write → skills table INSERT/UPDATE
Skill Use (runtime): Agent loads skill → skill_usage table INSERT
Skill Version Change: File backup + replace → skill_versions table UPDATE
```

### Backward Compatibility

- Existing `skill_feedback` table unchanged
- Legacy file-based versions (`SKILL_v*.md`, `versions/vN/`) migrated on startup to flat `{skill_name}@vN/` structure
- `load_skills()` updated to skip `@vN` directories (filter: skip if `@v` in directory name)
- All existing endpoints continue to function
- List endpoint gains optional query params for filtering (defaults to current behavior)

---

## Database Schema

### 1. `skills` — Skill Registry

```sql
CREATE TABLE IF NOT EXISTS skills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name  TEXT NOT NULL UNIQUE,
    source      TEXT NOT NULL DEFAULT 'personal',  -- 'shared' | 'personal'
    owner_id    TEXT NOT NULL DEFAULT '',           -- user_id who owns/created
    description TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT '',           -- 'coding' | 'data' | 'writing' | 'analysis' | 'ops' | ''
    tags        TEXT NOT NULL DEFAULT '[]',         -- JSON array: ["python", "testing"]
    status      TEXT NOT NULL DEFAULT 'active',     -- 'active' | 'deprecated' | 'draft'
    version     TEXT NOT NULL DEFAULT '',           -- current version string (e.g. "v2")
    path        TEXT NOT NULL DEFAULT '',           -- full directory path on filesystem
    created_at  REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at  REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_skills_source ON skills(source);
CREATE INDEX IF NOT EXISTS idx_skills_owner ON skills(owner_id);
CREATE INDEX IF NOT EXISTS idx_skills_category ON skills(category);
CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status);
```

### 2. `skill_usage` — Usage Tracking

```sql
CREATE TABLE IF NOT EXISTS skill_usage (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name     TEXT NOT NULL REFERENCES skills(skill_name),
    user_id        TEXT NOT NULL DEFAULT '',
    session_id     TEXT NOT NULL DEFAULT '',
    version_number INTEGER NOT NULL DEFAULT 0,
    action         TEXT NOT NULL DEFAULT 'use',  -- 'load' | 'use' | 'view'
    created_at     REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_usage_skill ON skill_usage(skill_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_user ON skill_usage(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_session ON skill_usage(session_id);
```

### 3. `skill_versions` — Version Metadata

```sql
CREATE TABLE IF NOT EXISTS skill_versions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name     TEXT NOT NULL REFERENCES skills(skill_name),
    version_number INTEGER NOT NULL,
    path           TEXT NOT NULL DEFAULT '',   -- flat directory: {skill_name}@vN/
    change_summary TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'active' | 'rolled_back'
    created_by     TEXT NOT NULL DEFAULT 'user',      -- 'user' | 'agent' | 'upload'
    file_count     INTEGER NOT NULL DEFAULT 1,
    created_at     REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_versions_skill ON skill_versions(skill_name, version_number DESC);
CREATE UNIQUE INDEX idx_versions_unique ON skill_versions(skill_name, version_number);
```

---

## API Changes

### New Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/skills` | any | List all skills with optional filters: `?category=&tag=&source=&status=&owner=` |
| GET | `/api/skills/{skill_name}/usage` | any | Get usage stats: total_uses, unique_users, recent_sessions, per-version breakdown |
| POST | `/api/skills/{skill_name}/usage` | — | Record a usage event (called by agent subprocess) |
| GET | `/api/admin/skills/manage` | admin | Admin management: bulk status changes, category/tag assignment, deprecate |
| PUT | `/api/admin/skills/{skill_name}/meta` | admin | Update category, tags, description, status |
| DELETE | `/api/admin/skills/{skill_name}` | admin | Hard delete skill from DB (optionally also from filesystem) |

### Modified Endpoints

| Endpoint | Change |
|----------|--------|
| `GET /api/shared-skills` | No change — returns SkillInfo[] from filesystem as before |
| `GET /api/users/{user_id}/skills` | No change — returns SkillInfo[] from filesystem as before |
| `GET /api/skills/{skill_name}/analytics` | Extend response to include usage data from `skill_usage` |
| `POST /api/users/{user_id}/skills/upload` | On upload, also INSERT into `skills` table |
| `POST /api/shared-skills/upload` | On upload, also INSERT into `skills` table |
| `POST /api/skills/{skill_name}/activate-version` | Update `skill_versions` status + `skills.version` |
| `POST /api/skills/{skill_name}/evolve-agent` | Record new version in `skill_versions` with `created_by='agent'` |

---

## Components

### `src/skill_manager.py` (NEW)

Core manager class (pattern follows `src/skill_feedback.py`):

```python
class SkillManager:
    """Database-backed skill registry, usage tracking, and version metadata."""

    def __init__(self, db: Database) -> None: ...

    # Registry
    async def register_skill(self, skill_name: str, source: str, owner_id: str,
                             description: str, category: str, tags: list[str]) -> None
    async def update_skill_meta(self, skill_name: str, **kwargs) -> None
    async def get_skill(self, skill_name: str) -> dict | None
    async def list_skills(self, source=None, category=None, tag=None,
                          status=None, owner=None) -> list[dict]
    async def delete_skill(self, skill_name: str, *, delete_files: bool = False) -> None

    # Usage
    async def record_usage(self, skill_name: str, user_id: str, session_id: str,
                           version_number: int = 0, action: str = "use") -> None
    async def get_usage_stats(self, skill_name: str) -> dict
    async def get_top_skills(self, limit: int = 10) -> list[dict]

    # Versions
    async def record_version(self, skill_name: str, version_number: int,
                             path: str, change_summary: str, created_by: str,
                             file_count: int = 1) -> None
    async def activate_version(self, skill_name: str, version_number: int) -> dict | None
    async def list_versions(self, skill_name: str) -> list[dict]
```

### Startup Migration

In `main_server.py` startup hook (`lifespan` function):
- Scan filesystem for all skills (shared + personal)
- For each skill not in `skills` table: INSERT
- Migrate legacy nested versions (`SKILL_v*.md`, `versions/vN/`) to flat `{skill_name}@vN/` directories
- Existing skills in DB but missing from filesystem: mark as `status='deprecated'`

### `load_skills()` Update

In `main_server.py` `load_skills()`:
- Filter out directories matching `{name}@vN` pattern (historical versions)
- Keep existing behavior: only read `SKILL.md` from root-level directories

### Usage Recording Hook

In `agent_server.py` where skills are loaded (the `load_skills` function):
- After loading a skill from filesystem, call `SkillManager.record_usage()`
- This is async fire-and-forget — don't block agent if DB is unavailable

---

## Error Handling

- DB unavailable: usage recording is fire-and-forget (try/except with silent fail)
- Skill not in DB during usage: auto-register with defaults (source='unknown', owner='')
- Duplicate skill registration: ON CONFLICT UPDATE (idempotent)
- Delete skill: foreign key behavior — `skill_usage` rows preserved (historical), `skill_versions` rows preserved (historical)

## Security

- Usage recording is internal (agent → API) — no user-facing input
- Admin endpoints require admin JWT via `require_admin`
- `skill_name` validated against path traversal (`../`, null bytes)
- Tags validated as valid JSON array with string elements only

## Testing

| Test File | Scope |
|-----------|-------|
| `tests/unit/test_skill_manager.py` | SkillManager CRUD operations |
| `tests/unit/test_skill_usage.py` | Usage recording, stats aggregation |
| `tests/unit/test_skill_versions.py` | Version lifecycle: record, activate, rollback |
| `tests/integration/test_skill_migration.py` | Startup migration from filesystem to DB |
| `tests/unit/test_skills_api.py` | New REST endpoints |

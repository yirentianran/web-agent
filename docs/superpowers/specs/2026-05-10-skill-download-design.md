# Skill Download Feature Design

**Date**: 2026-05-10
**Author**: Claude Code

## Problem Statement

Users need to download skills as ZIP archives. Ordinary users can download their own personal skills. Admin users can download shared skills and any user's personal skills.

## Scope

- Backend: new download endpoint, permission model expansion, skill metadata tracking
- Frontend: download buttons on Personal Skills and Shared Skills pages

Out of scope: skill upload modifications (existing), skill promotion, skill evolution.

## Architecture

### Backend Changes

#### 1. SkillInfo Model Update

`src/models.py` — add `owner` field:

```python
class SkillInfo(BaseModel):
    name: str
    source: SkillSource         # "shared" or "personal"
    owner: str = ""             # NEW: user_id who owns/uploaded the skill
    description: str = ""
    content: str = ""
    path: str = ""
    created_at: str = ""
    created_by: str = ""        # "upload" | "skill-creator"
    valid: bool = True
```

#### 2. Skill Metadata Tracking

On skill upload (both personal and shared), write `owner` into `skill-meta.json`:
- Personal skills: `owner` = authenticated user's `user_id`
- Shared skills: `owner` = authenticated user's `user_id`

Modify `_read_skill_meta` in `main_server.py` to also read `owner` from `skill-meta.json`.

#### 3. Personal Skills List Endpoint — Permission Expansion

`GET /api/users/{user_id}/skills`

Current behavior: returns only the user's own personal skills.

New behavior:
- If caller is admin: return **all** personal skills from all users (scan all `data/users/*/workspace/.claude/skills/` directories)
- If caller is regular user: return only their own personal skills (existing behavior)

Each returned skill includes the `owner` field so the frontend can display who owns it.

#### 4. New Download Endpoint

`GET /api/skills/download/{source}/{skill_name}`

**Path parameters:**
- `source`: `"shared"` or `"personal"`
- `skill_name`: name of the skill (path segment)

**Query parameters:**
- `owner` (optional): for personal skills, the user_id of the skill owner. Required when downloading personal skills.

**Permission matrix:**

| Source | Caller | Allowed? |
|--------|--------|----------|
| `shared` | admin | Yes |
| `shared` | regular user | No (403) |
| `personal` (own) | regular user | Yes |
| `personal` (anyone) | admin | Yes |

**Response:** `application/zip` stream, filename `skill_name.zip`

**ZIP contents:** complete skill directory — `SKILL.md`, `skill-meta.json`, `scripts/`, `references/`, `assets/`, etc.

**Implementation:**
- Resolve skill path based on `source` and `owner`
- Validate access with permission check above
- Use Python's `zipfile` module to stream the ZIP
- Use `StreamingResponse` to avoid loading entire ZIP into memory

### Frontend Changes

#### Personal Skills Page (`SkillsPage.tsx`)

- Add a download button (ZIP icon) to each skill card
- Display the owner name on each skill card (for admin view showing others' skills)
- No separate admin view — same UI for all users
- On click: fetch `GET /api/skills/download/personal/{skill_name}?owner={owner}`, trigger browser download

#### Shared Skills Page

- Already admin-only (existing logic)
- Add a download button to each skill card
- On click: fetch `GET /api/skills/download/shared/{skill_name}`, trigger browser download

#### API Hook (`useSkillsApi.ts`)

Add `downloadSkill(source, skillName, owner?)` function that:
1. Constructs the download URL
2. Makes a `GET` request with auth token
3. Creates a Blob from the response
4. Triggers browser download via temporary `<a>` element

## Data Flow

```
User clicks Download
  → Frontend calls GET /api/skills/download/{source}/{name}?owner={owner}
    → Backend validates JWT + permission
      → Resolves skill directory path
      → Creates ZIP in memory stream
    ← Returns application/zip stream
  → Browser downloads {skill_name}.zip
```

## Error Handling

| Error | Status | Frontend behavior |
|-------|--------|-------------------|
| Skill not found | 404 | Show toast "Skill not found" |
| Permission denied | 403 | Show toast "You don't have permission to download this skill" |
| Auth required | 401 | Redirect to login |
| Server error | 500 | Show toast "Download failed" |

## Testing

### Backend Tests
- Test download permission matrix (admin vs regular user, own vs others' skills, shared vs personal)
- Test ZIP contains all expected files
- Test 404 for non-existent skills
- Test 403 for unauthorized access

### Frontend Tests
- Test download button renders on skill cards
- Test download triggers browser file download
- Test admin view shows all personal skills with owner labels

## Dependencies

- Existing JWT auth (`src/auth.py`, `src/admin_auth.py`)
- Existing skill listing endpoints in `main_server.py`
- Existing `SkillInfo` model in `src/models.py`
- Python `zipfile` (stdlib)

# Skill Download Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ZIP download for skills — ordinary users download their own personal skills, admins download shared skills and any user's personal skills.

**Architecture:** Extend existing skill listing endpoints to include owner info, add a new download endpoint that streams ZIPs, and add download buttons to the frontend Skills page.

**Tech Stack:** FastAPI, Python zipfile, React/TypeScript, Fetch API

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/models.py:70-78` | Modify | Add `owner` field to `SkillInfo` model |
| `main_server.py:3811-3820` | Modify | Extend `_read_skill_meta` to return `owner` |
| `main_server.py:3990-4001` | Modify | Write `owner` on personal skill upload |
| `main_server.py:4031-4042` | Modify | Write `owner` on shared skill upload |
| `main_server.py:3823-3863` | Modify | Expand personal skills list to return all skills for admins |
| `main_server.py` (new ~60 lines) | Create | `GET /api/skills/download/{source}/{skill_name}` endpoint |
| `frontend/src/lib/types.ts:65-76` | Modify | Add `owner` field to `Skill` interface |
| `frontend/src/hooks/useSkillsApi.ts` | Modify | Add `downloadSkill()` method |
| `frontend/src/components/SkillsPage.tsx` | Modify | Add download button per skill card |
| `tests/unit/test_main_server.py` | Modify | Add tests for download and owner listing |

---

### Task 1: Add `owner` field to SkillInfo model

**Files:**
- Modify: `src/models.py:70-78`

- [ ] **Step 1: Add `owner` field to SkillInfo**

```python
class SkillInfo(BaseModel):
    name: str
    source: SkillSource
    owner: str = ""             # user_id who owns/uploaded the skill
    description: str = ""
    content: str = ""
    path: str = ""
    created_at: str = ""        # ISO 8601 timestamp
    created_by: str = ""        # "upload" | "skill-creator"
    valid: bool = True          # False when SKILL.md is missing or unparseable
```

- [ ] **Step 2: Commit**

```bash
git add src/models.py
git commit -m "feat: add owner field to SkillInfo model"
```

---

### Task 2: Extend `_read_skill_meta` to return owner

**Files:**
- Modify: `main_server.py:3811-3820`

- [ ] **Step 1: Update `_read_skill_meta` to return 3 values**

Current function at lines 3811-3820:

```python
def _read_skill_meta(skill_dir: Path) -> tuple[str, str]:
    """Read skill-meta.json, return (created_at, created_by). Defaults if missing."""
    meta_path = skill_dir / "skill-meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            return meta.get("created_at", ""), meta.get("source", "")
        except (json.JSONDecodeError, OSError):
            pass
    return "", ""
```

Replace with:

```python
def _read_skill_meta(skill_dir: Path) -> tuple[str, str, str]:
    """Read skill-meta.json, return (created_at, created_by, owner). Defaults if missing."""
    meta_path = skill_dir / "skill-meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            return meta.get("created_at", ""), meta.get("source", ""), meta.get("owner", "")
        except (json.JSONDecodeError, OSError):
            pass
    return "", "", ""
```

- [ ] **Step 2: Update all callers of `_read_skill_meta`**

There are 3 call sites that unpack the return value. Update each from 2-variable to 3-variable unpacking:

1. In `list_shared_skills` (~line 3790): change `created_at, created_by = _read_skill_meta(d)` to `created_at, created_by, owner = _read_skill_meta(d)`
2. In `list_user_skills` (~line 3840): same change
3. In any other caller that uses `_read_skill_meta`

Then add `owner=owner` to each `SkillInfo(...)` construction.

Find all occurrences with: `grep -n "_read_skill_meta" main_server.py`

- [ ] **Step 3: Commit**

```bash
git add main_server.py
git commit -m "feat: extend _read_skill_meta to return owner field"
```

---

### Task 3: Write `owner` on skill upload

**Files:**
- Modify: `main_server.py:3990-4001` (personal upload)
- Modify: `main_server.py:4031-4042` (shared upload)

- [ ] **Step 1: Update personal skill upload to write owner**

In `upload_skill_files` (~line 3990-4001), the skill-meta.json write block looks like:

```python
(skill_dir / "skill-meta.json").write_text(
    json.dumps({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "upload",
        "zip_filename": zip_filename,
    })
)
```

Change to include owner (the `current_user` param is the authenticated user_id):

```python
(skill_dir / "skill-meta.json").write_text(
    json.dumps({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "upload",
        "owner": current_user,
        "zip_filename": zip_filename,
    })
)
```

- [ ] **Step 2: Update shared skill upload to write owner**

In `upload_shared_skill` (~line 4031-4042), same pattern. The function has `current_user: str = Depends(require_admin)`:

```python
(skill_dir / "skill-meta.json").write_text(
    json.dumps({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "upload",
        "owner": current_user,
        "zip_filename": zip_filename,
    })
)
```

- [ ] **Step 3: Commit**

```bash
git add main_server.py
git commit -m "feat: write owner field in skill-meta.json on upload"
```

---

### Task 4: Expand personal skills list for admin users

**Files:**
- Modify: `main_server.py:3823-3863` (`list_user_skills` endpoint)

- [ ] **Step 1: Modify `list_user_skills` to return all skills for admins**

Current behavior: only lists skills from `user_id`'s workspace.

New behavior: if `current_user` (the authenticated caller) is an admin, scan all user directories under `DATA_ROOT / "users"` and collect personal skills from each.

The endpoint already imports `require_admin` — but we don't want to require admin, just check. Use this pattern:

```python
@app.get("/api/users/{user_id}/skills", response_model=list[SkillInfo])
async def list_user_skills(
    user_id: str,
    current_user: str = Depends(get_current_user),
) -> list[SkillInfo]:
    """List personal skills.

    Admin callers see all users' skills; regular callers see only their own.
    """
    # Determine which directories to scan
    skills_dir = DATA_ROOT / "users"
    if current_user != user_id:
        # Cross-user access: only allowed for admins
        try:
            admin_id = require_admin(current_user)  # raises 403 if not admin
        except Exception:
            return JSONResponse({"error": "forbidden"}, status_code=403)

    # Build list of (dir, owner_id) pairs to scan
    if _is_admin(current_user):
        # Scan all user directories
        user_dirs = []
        if skills_dir.exists():
            for d in sorted(skills_dir.iterdir()):
                if d.is_dir():
                    user_dirs.append((d, d.name))
    else:
        # Only own directory
        user_dirs = [(skills_dir / user_id, user_id)]

    results: list[SkillInfo] = []
    for user_dir, owner_id in user_dirs:
        skill_base = user_dir / "workspace" / ".claude" / "skills"
        if not skill_base.exists():
            continue
        for d in sorted(skill_base.iterdir()):
            if not d.is_dir() or d.is_symlink():
                continue
            # Skip shared-skill copies
            if (d.parent.parent / "shared-skills" / d.name).exists():
                continue
            skill_md = d / "SKILL.md"
            if not skill_md.exists():
                continue
            created_at, created_by, owner = _read_skill_meta(d)
            results.append(
                SkillInfo(
                    name=d.name,
                    source=SkillSource.PERSONAL,
                    owner=owner,
                    description="",
                    content=skill_md.read_text(),
                    path=str(d),
                    created_at=created_at,
                    created_by=created_by,
                    valid=True,
                )
            )
    return results
```

Note: `_is_admin` helper — check if `main_server.py` already has an `is_admin` or `_is_admin` function. If not, add a simple one:

```python
def _is_admin(user_id: str) -> bool:
    """Check if user_id corresponds to an admin role."""
    # Use the same JWT decode logic as require_admin
    from src.auth import _decode_token  # or wherever the token decode lives
    token = ...  # we need to get the token from the request
    ...
```

Actually, since `current_user` is already just the `user_id` string (from `get_current_user`), we need a way to check admin status. Look at how `require_admin` works — it decodes the JWT and checks `role == "admin"`. We need a non-raising version. Add this helper to `src/admin_auth.py`:

```python
def is_admin(user_id: str, token: str | None = None) -> bool:
    """Return True if the given user has admin role. Does not raise."""
    # Reuse the JWT decode logic from require_admin but return bool
    ...
```

**Simpler approach:** Since `admin_auth.py` already has `ENFORCE_AUTH`, `JWT_SECRET`, and `ALGORITHM` as module-level constants, add a non-raising helper directly in `src/admin_auth.py`:

```python
def is_admin_request(authorization: str | None = None) -> bool:
    """Return True if the request carries a valid admin JWT. Never raises."""
    if not ENFORCE_AUTH:
        return True  # dev mode: allow everything
    if not authorization or not authorization.startswith("Bearer "):
        return False
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        return payload.get("role") == "admin"
    except Exception:
        return False
```

Then in the download endpoint, accept `authorization: str | None = Header(None)` and call `is_admin_request(authorization)`.

Similarly, in `list_user_skills`, accept `authorization: str | None = Header(None)` and use `is_admin_request(authorization)` to decide whether to scan all user directories.

- [ ] **Step 2: Add `is_admin_request` helper to `src/admin_auth.py`**

Add after the `require_admin` function (~line 71):

```python
def is_admin_request(authorization: str | None = None) -> bool:
    """Return True if the request carries a valid admin JWT. Never raises."""
    if not ENFORCE_AUTH:
        return True
    if not authorization or not authorization.startswith("Bearer "):
        return False
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        return payload.get("role") == "admin"
    except Exception:
        return False
```

- [ ] **Step 3: Modify `list_user_skills` endpoint**

In `main_server.py`, find the `list_user_skills` function (~line 3823-3863). Add `authorization: str | None = Header(None)` as a parameter. Import `is_admin_request` from `src.admin_auth`.

Replace the current logic with:

```python
@app.get("/api/users/{user_id}/skills", response_model=list[SkillInfo])
async def list_user_skills(
    user_id: str,
    current_user: str = Depends(get_current_user),
    authorization: str | None = Header(None),
) -> list[SkillInfo]:
    """List personal skills.

    Admin callers see all users' skills; regular callers see only their own.
    """
    from src.admin_auth import is_admin_request

    is_admin = is_admin_request(authorization)

    # Cross-user access check: non-admin accessing another user's skills
    if not is_admin and current_user != user_id:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    skills_dir = DATA_ROOT / "users"

    # Build list of (dir, owner_id) pairs to scan
    if is_admin:
        user_dirs: list[tuple[Path, str]] = []
        if skills_dir.exists():
            for d in sorted(skills_dir.iterdir()):
                if d.is_dir():
                    user_dirs.append((d, d.name))
    else:
        user_dirs = [(skills_dir / user_id, user_id)]

    results: list[SkillInfo] = []
    for user_dir, owner_id in user_dirs:
        skill_base = user_dir / "workspace" / ".claude" / "skills"
        if not skill_base.exists():
            continue
        for d in sorted(skill_base.iterdir()):
            if not d.is_dir() or d.is_symlink():
                continue
            skill_md = d / "SKILL.md"
            if not skill_md.exists():
                continue
            created_at, created_by, owner = _read_skill_meta(d)
            results.append(
                SkillInfo(
                    name=d.name,
                    source=SkillSource.PERSONAL,
                    owner=owner,
                    description="",
                    content=skill_md.read_text(),
                    path=str(d),
                    created_at=created_at,
                    created_by=created_by,
                    valid=True,
                )
            )
    return results
```

- [ ] **Step 4: Commit**

```bash
git add main_server.py src/admin_auth.py
git commit -m "feat: admin sees all personal skills in list endpoint"
```

---

### Task 5: Add skill download endpoint

**Files:**
- Create: new endpoint in `main_server.py` (after existing skill endpoints, ~line 4044)

- [ ] **Step 1: Add imports if needed**

`zipfile` is already imported (line 29). Add these to the existing imports:

Line 36 — add `StreamingResponse` to the `fastapi.responses` import:

```python
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
```

After line 29 (or with other stdlib imports), add:

```python
from io import BytesIO
```

- [ ] **Step 2: Add the download endpoint**

Add after the existing skill upload endpoints (~line 4044):

```python
@app.get("/api/skills/download/{source}/{skill_name}")
async def download_skill(
    source: str,
    skill_name: str,
    owner: str | None = None,
    authorization: str | None = Header(None),
    current_user: str = Depends(get_current_user),
):
    """Download a skill as a ZIP archive.

    Permissions:
    - shared: admin only
    - personal (own): regular user
    - personal (anyone): admin
    """
    from src.admin_auth import is_admin_request

    if source not in ("shared", "personal"):
        return JSONResponse({"error": "invalid source, must be 'shared' or 'personal'"}, status_code=400)

    admin = is_admin_request(authorization)

    if source == "shared":
        if not admin:
            return JSONResponse({"error": "forbidden: admin required for shared skills"}, status_code=403)
        skill_dir = DATA_ROOT / "shared-skills" / skill_name
    else:
        if not owner:
            return JSONResponse({"error": "owner query param required for personal skills"}, status_code=400)
        skill_dir = DATA_ROOT / "users" / owner / "workspace" / ".claude" / "skills" / skill_name

        if current_user != owner and not admin:
            return JSONResponse({"error": "forbidden"}, status_code=403)

    if not skill_dir.exists() or not skill_dir.is_dir():
        return JSONResponse({"error": "skill not found"}, status_code=404)

    # Build ZIP in memory
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in skill_dir.rglob("*"):
            if file_path.is_file():
                arcname = f"{skill_name}/{file_path.relative_to(skill_dir)}"
                zf.write(file_path, arcname)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{skill_name}.zip"'},
    )
```

Note: `Header` is already imported from `fastapi` in `main_server.py` (verify with `grep -n "from fastapi" main_server.py`). If not, add it.

- [ ] **Step 3: Commit**

```bash
git add main_server.py src/admin_auth.py
git commit -m "feat: add skill download ZIP endpoint"
```

---

### Task 6: Update frontend types and API hook

**Files:**
- Modify: `frontend/src/lib/types.ts:65-76`
- Modify: `frontend/src/hooks/useSkillsApi.ts`

- [ ] **Step 1: Add `owner` to Skill type**

In `frontend/src/lib/types.ts`, update the `Skill` interface:

```typescript
export interface Skill {
  name: string
  source: SkillSource
  owner: string
  description: string
  content: string
  path: string
  created_at: string
  created_by: string
  valid: boolean
}
```

- [ ] **Step 2: Add `downloadSkill` to useSkillsApi**

Read `frontend/src/hooks/useSkillsApi.ts` first. The hook exposes methods via return. Add a `downloadSkill` method:

```typescript
const downloadSkill = async (source: 'shared' | 'personal', skillName: string, owner?: string): Promise<void> => {
  const params = new URLSearchParams()
  if (owner) params.set('owner', owner)

  const url = `${API_BASE}/skills/download/${source}/${encodeURIComponent(skillName)}?${params}`

  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${authToken}` },
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ error: 'Download failed' }))
    throw new Error(error.error || 'Download failed')
  }

  const blob = await response.blob()
  const downloadUrl = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = downloadUrl
  a.download = `${skillName}.zip`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  window.URL.revokeObjectURL(downloadUrl)
}
```

Add to the return object:

```typescript
return {
  // ... existing methods
  downloadSkill,
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/hooks/useSkillsApi.ts
git commit -m "feat: add downloadSkill to API hook and owner to Skill type"
```

---

### Task 7: Add download button to SkillsPage

**Files:**
- Modify: `frontend/src/components/SkillsPage.tsx`

- [ ] **Step 1: Add download handler and button**

In `SkillsPage.tsx`, add a download callback after the existing handlers:

```typescript
const handleDownload = useCallback(async (skill: Skill) => {
  try {
    await api.downloadSkill(skill.source, skill.name, skill.owner || undefined)
  } catch (e) {
    setError(e instanceof Error ? e.message : t('skills.downloadFailed'))
  }
}, [api, t])
```

Add the download button in the skill actions section (inside the `.skill-actions` div, before or after the view button). In the skill card rendering (~line 159-167), add a download button:

```tsx
<div className="skill-actions">
  <button className="skill-download-btn" onClick={() => handleDownload(skill)} type="button">
    {t('skills.download')}
  </button>
  {skill.valid && (
    <button className="skill-view-btn" onClick={() => setViewingSkill(skill)} type="button">{t('common.view')}</button>
  )}
  {isPersonal && skill.valid && (
    <button className="skill-promote-btn" onClick={() => handlePromote(skill.name)} type="button" disabled={promoting === skill.name}>
      {promoting === skill.name ? t('common.promoting') : t('common.promote')}
    </button>
  )}
  <button className="skill-delete-btn" onClick={() => handleDelete(skill.name)} type="button">{t('common.delete')}</button>
</div>
```

- [ ] **Step 2: Add CSS for download button**

Check if there's a CSS file for SkillsPage. Look for styles in `frontend/src/components/SkillsPage.css` or similar. Add a style for `.skill-download-btn` that matches the existing `.skill-view-btn` style.

If styles are inline or in a shared file, follow the existing pattern. The button should visually match the other action buttons.

- [ ] **Step 3: Add i18n key**

Check for i18n translation files (likely `frontend/src/locales/` or similar). Add `skills.download` key. If the project uses a specific pattern, follow it. Add at minimum an English fallback.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/SkillsPage.tsx
git commit -m "feat: add download button to skill cards"
```

---

### Task 8: Add backend tests

**Files:**
- Modify: `tests/unit/test_main_server.py`

- [ ] **Step 1: Add test class for skill download**

Add after the existing `TestSkillsAPI` class (~line 604):

```python
class TestSkillDownload:
    def test_download_personal_skill_as_owner(self, client: TestClient) -> None:
        """Owner can download their own personal skill."""
        # Create a skill first
        client.post(
            "/api/users/alice/skills",
            json={"name": "download-test", "content": "# Test", "description": "test skill"},
        )
        resp = client.get("/api/skills/download/personal/download-test?owner=alice")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"

    def test_download_shared_skill_as_admin(self, client: TestClient) -> None:
        """Admin can download shared skills."""
        # Upload a shared skill first (requires admin auth header)
        # In test mode without ENFORCE_AUTH, this endpoint is open.
        # Create the skill directory manually since upload endpoint may require admin:
        shared_dir = main_server.DATA_ROOT / "shared-skills" / "admin-skill"
        shared_dir.mkdir(parents=True, exist_ok=True)
        (shared_dir / "SKILL.md").write_text("# Admin Skill")
        (shared_dir / "skill-meta.json").write_text(json.dumps({
            "created_at": "2026-05-10T00:00:00+00:00",
            "source": "upload",
            "owner": "admin-user",
        }))
        resp = client.get("/api/skills/download/shared/admin-skill")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        # Verify ZIP contains the skill files
        import zipfile
        buf = BytesIO(resp.content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            assert any("SKILL.md" in n for n in names)

    def test_download_others_skill_forbidden(self, client: TestClient) -> None:
        """Regular user cannot download another user's personal skill."""
        client.post(
            "/api/users/alice/skills",
            json={"name": "alice-skill", "content": "# Alice", "description": ""},
        )
        # bob trying to download alice's skill should be 403
        resp = client.get("/api/skills/download/personal/alice-skill?owner=alice")
        assert resp.status_code == 403

    def test_download_nonexistent_skill(self, client: TestClient) -> None:
        resp = client.get("/api/skills/download/personal/no-such-skill?owner=alice")
        assert resp.status_code == 404

    def test_download_invalid_source(self, client: TestClient) -> None:
        resp = client.get("/api/skills/download/invalid/skill?owner=alice")
        assert resp.status_code == 400
```

Note: The tests use `TestClient` without auth tokens because the test fixture doesn't set up auth (ENFORCE_AUTH is likely off in tests). The permission checks may not trigger in tests. Adjust the tests based on whether auth is enforced in the test environment. Check if there's a way to pass auth headers via `client.get(..., headers={...})`.

- [ ] **Step 2: Run tests**

```bash
cd /Users/mac/Documents/Projects/web-agent
uv run pytest tests/unit/test_main_server.py::TestSkillDownload -v
```

Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_main_server.py
git commit -m "test: add skill download endpoint tests"
```

---

### Task 9: Run full test suite and verify

- [ ] **Step 1: Run full backend test suite**

```bash
cd /Users/mac/Documents/Projects/web-agent
uv run pytest tests/unit/test_main_server.py -v
```

Expected: All tests pass.

- [ ] **Step 2: Run frontend type check**

```bash
cd /Users/mac/Documents/Projects/web-agent/frontend
npx tsc --noEmit
```

Expected: No type errors.

- [ ] **Step 3: Commit if any fixes needed**

```bash
git add -A
git commit -m "fix: address test and type check feedback"
```

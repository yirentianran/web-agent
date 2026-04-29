# Multi-User Isolation Design

## Overview

Web Agent supports multiple users with isolated sessions, workspaces, memory, and runtime environments. This document defines the isolation boundaries, enforcement mechanisms, and security model.

## Isolation Layers

```
┌─────────────────────────────────────────────────────────────┐
│                    Isolation Layers                          │
├─────────────────────────────────────────────────────────────┤
│ Layer 1: Identity & Authentication                          │
│   JWT-based, bcrypt password, token-scoped to user_id       │
├─────────────────────────────────────────────────────────────┤
│ Layer 2: Session Isolation                                  │
│   Sessions keyed by (user_id, session_id), cross-checked    │
├─────────────────────────────────────────────────────────────┤
│ Layer 3: Workspace Isolation                                │
│   Per-user filesystem, tool hooks enforce                   │
├─────────────────────────────────────────────────────────────┤
│ Layer 4: Message Buffer Isolation                           │
│   In-memory buffer segmented by user, WebSocket filtering   │
├─────────────────────────────────────────────────────────────┤
│ Layer 5: Memory Isolation                                   │
│   L1 (SQLite) + L2 (Markdown files), both scoped to user_id │
├─────────────────────────────────────────────────────────────┤
│ Layer 6: Database Isolation                                 │
│   SQLite with user_id FK, queries always filter by user_id  │
├─────────────────────────────────────────────────────────────┤
│ Layer 7: Container Isolation (Production Required)          │
│   Per-user Docker container, hardware-level separation      │
└─────────────────────────────────────────────────────────────┘
```

---

## Layer 1: Identity & Authentication

### Token Flow

```
Client                    Main Server                  Agent Server
  │                           │                            │
  │  POST /api/auth/token     │                            │
  │  {user_id, password}      │                            │
  │ ─────────────────────────>│                            │
  │                           │  verify_password +         │
  │                           │  create_token(user_id)     │
  │  {token, user_id}         │                            │
  │ <─────────────────────────│                            │
  │                           │                            │
  │  All subsequent requests  │                            │
  │  Authorization: Bearer    │                            │
  │  <token>                  │                            │
  │ ─────────────────────────>│  verify_token(token)       │
  │                           │  → user_id                 │
```

### Enforcement

| Mode | `ENFORCE_AUTH` | Behavior |
|------|---------------|----------|
| Development | `false` | All requests treated as `user_id="default"`, no token required |
| Production | `true` | JWT required on every request, verified via `verify_token()` |

### Token Claims

```json
{
  "sub": "user_id",
  "role": "user",
  "iat": 1714230000,
  "exp": 1714316400
}
```

- **`sub`**: Canonical user identifier, immutable for token lifetime
- **`role`**: `user` or `admin` (admin gated separately)
- **`iat`**: Issued-at timestamp
- **`exp`**: Expiration (default 24h)

### CSRF Protection

All current APIs use `Authorization: Bearer <token>` header authentication, not cookies, so they are naturally immune to CSRF. If cookie-based auth is introduced in the future (e.g., OAuth callback), CSRF token validation must be added.

### Token Extraction (Dual Method)

`get_current_user` accepts tokens from two sources, checked in priority order:

1. **`Authorization: Bearer <token>` header** (primary, for REST endpoints)
2. **`?token=<jwt>` query parameter** (fallback, for WebSocket connections)

```python
def get_current_user(
    authorization: str | None = Header(None),
    token: str | None = Query(None),
) -> str:
    if not ENFORCE_AUTH:
        return "default"
    if authorization and authorization.startswith("Bearer "):
        return verify_token(authorization.split(" ", 1)[1])
    if token:
        return verify_token(token)
    raise HTTPException(status_code=401, detail="Missing authentication token")
```

When `ENFORCE_AUTH=false` (development), always returns `"default"` — no token required.

### Path Parameter Verification (CRITICAL)

Every endpoint accepting `{user_id}` in the URL path MUST use the two-line auth pattern:

1. Add `current_user: str = Depends(get_current_user)` to the function signature
2. Call `verify_path_user(user_id, current_user)` as the first body line

```python
@router.get("/api/users/{user_id}/sessions")
async def list_sessions(
    user_id: str,
    current_user: str = Depends(get_current_user),
):
    verify_path_user(user_id, current_user)  # ← REQUIRED
    store = SessionStore()
    return store.list_sessions(user_id)
```

`verify_path_user` is a thin wrapper that delegates to `require_user_match`, which is a no-op when `ENFORCE_AUTH=false`.

All 35 endpoints containing `{user_id}` in the URL path MUST follow this pattern.

### Identity Verification

`POST /api/auth/token` requires both `user_id` and `password` fields. The password is verified against a bcrypt hash stored in the `users` table:

```python
@router.post("/api/auth/token")
async def login(request: LoginRequest):
    row = db.execute(
        "SELECT password_hash, role FROM users WHERE user_id = ?",
        (request.user_id,)
    ).fetchone()
    if not row or not verify_password(request.password, row[0]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token(request.user_id, row[1])
    return {"token": token, "user_id": request.user_id}
```

Password hashing uses bcrypt (via the `bcrypt` package, NOT the unmaintained `passlib`). Empty password_hash (for auto-created dev users) always fails verification — these users can only operate in `ENFORCE_AUTH=false` mode.

### User Registration

`POST /api/auth/register` creates a new user with a bcrypt-hashed password:

```python
@router.post("/api/auth/register")
async def register(request: RegisterRequest):
    if len(request.password) < 8:
        raise HTTPException(status_code=400, detail="Password too short")
    password_hash = hash_password(request.password)
    db.execute(
        "INSERT INTO users (user_id, password_hash, role) VALUES (?, ?, 'user')",
        (request.user_id, password_hash)
    )
    token = create_token(request.user_id, role="user")
    return {"token": token, "user_id": request.user_id}
```

If a user_id already exists, returns 409 Conflict.

### Auto-Created Users (Dev Mode)

When `ENFORCE_AUTH=false`, `SessionStore.create_session()` auto-creates users via `INSERT OR IGNORE INTO users` with `password_hash=''` and `role='user'`. These users have no valid password and cannot authenticate when auth is enabled. An admin or the register endpoint must set a password before they can log in under `ENFORCE_AUTH=true`.

### Identity Provider Integration (Future)

For production multi-tenant deployments beyond the current password-based auth:
- **OAuth 2.0 / OIDC**: Google, GitHub, Microsoft
- **SAML**: Enterprise SSO
- **API Keys**: Service accounts for CI/CD or programmatic access

Current password verification is sufficient for Phase 1 internal use. OAuth is a Phase 3 item.

### User Lifecycle: Disable, Never Delete

User records are **never deleted**. Instead, users are disabled. This decision avoids cascading referential integrity issues across sessions, messages, uploads, generated_files, and audit_log — all of which reference `user_id`.

#### Status States

| Status | Meaning |
|--------|---------|
| `active` | Normal operation, can authenticate and use the system |
| `disabled` | Cannot authenticate, existing sessions immediately terminated |

No intermediate or quarantine states (e.g., `banned`, `suspended`) are needed — `active` / `disabled` is sufficient.

#### Disable Flow

```
Admin calls: PATCH /api/admin/users/{user_id}/disable
                │
                ├─ 1. SET status='disabled', disabled_at=now(), disabled_by=admin_user_id
                │
                ├─ 2. Enumerate all sessions WHERE user_id=? AND status='active'
                │     For each: mark session status='cancelled', call MessageBuffer.cancel()
                │     → WebSocket connections receive done/cancelled event, clients disconnect
                │
                └─ 3. Write audit_log entry: category='admin', action='user.disable'
```

#### Enable Flow

```
Admin calls: PATCH /api/admin/users/{user_id}/enable
                │
                ├─ 1. SET status='active', disabled_at=NULL, disabled_by=NULL
                │
                └─ 2. Write audit_log entry: category='admin', action='user.enable'
```

#### Auth Enforcement

`POST /api/auth/token` checks user status before issuing a token:

```python
@router.post("/api/auth/token")
async def login(request: LoginRequest):
    row = db.execute(
        "SELECT password_hash, role, status FROM users WHERE user_id = ?",
        (request.user_id,)
    ).fetchone()
    if not row or not verify_password(request.password, row[0]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if row[2] == "disabled":
        raise HTTPException(status_code=403, detail="Account disabled")
    token = create_token(request.user_id, row[1])
    return {"token": token, "user_id": request.user_id}
```

#### Admin Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/admin/users/{user_id}/disable` | PATCH | Disable a user, terminate all active sessions |
| `/api/admin/users/{user_id}/enable` | PATCH | Re-enable a previously disabled user |
| `/api/admin/users?status=disabled` | GET | List disabled users (existing list endpoint + filter) |

#### Key Design Constraints

- **No delete API exists** — There is no `DELETE /api/admin/users/{user_id}` endpoint. Users are never removed from the database.
- **Disabled users retain all data** — Sessions, messages, uploads, generated_files, and memory are preserved. The audit trail remains intact.
- **Disabled users consume no resources** — Active sessions are terminated. No new sessions can be created. The user occupies only storage, not compute.
- **Container cleanup** — If `CONTAINER_MODE=true`, the user's container is stopped (not destroyed) on disable, preserving volumes.

---

## Layer 2: Session Isolation

### Session Ownership Model

```
Session ID Format: session_{user_id}_{timestamp}_{uuid_hex}
Example:           session_yguo_1776176587.4795678_793030c6
```

Every session is **owned** by exactly one user. The `user_id` is embedded in the session ID and stored in the database.

### Database Schema

```sql
CREATE TABLE sessions (
    session_id  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(user_id),
    title       TEXT,
    status      TEXT DEFAULT 'active',
    cost_usd    REAL DEFAULT 0.0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_sessions_user_id ON sessions(user_id);
```

### Enforcement Rules

| Operation | Enforcement |
|-----------|------------|
| Create session | `user_id` from token, stored in DB |
| List sessions | `WHERE user_id = ?` filtered by authenticated user |
| Read history | `WHERE session_id = ? AND user_id = ?` — dual filter |
| Delete session | `WHERE session_id = ? AND user_id = ?` — dual filter |
| Update session | `WHERE session_id = ? AND user_id = ?` — dual filter |
| Cancel / Fork | `user_id` verified before operation |

### Session ID Format Validation

All operations accepting a `session_id` MUST validate the format matches `session_{user_id}_*` and that the embedded `user_id` matches the authenticated user:

```python
import re

SESSION_ID_PATTERN = re.compile(r'^session_([a-zA-Z0-9_.-]+)_\d+\.\d+_[a-f0-9]+$')

def validate_session_ownership(session_id: str, user_id: str) -> bool:
    m = SESSION_ID_PATTERN.match(session_id)
    if not m:
        return False
    return m.group(1) == user_id
```

This provides a fast-path ownership check before hitting the database, and catches malformed or spoofed session IDs early.

### SessionStore Methods (Required Signatures)

```python
class SessionStore:
    def create_session(self, user_id: str, session_id: str) -> None: ...
    def list_sessions(self, user_id: str) -> list[dict]: ...
    def get_session_history(self, user_id: str, session_id: str) -> list[dict]: ...
    def delete_session(self, user_id: str, session_id: str) -> None: ...
    def update_session_title(self, user_id: str, session_id: str, title: str) -> None: ...
    def update_session_status(self, user_id: str, session_id: str, status: str) -> None: ...
    def add_message(self, user_id: str, session_id: str, message: dict) -> None: ...
    def update_session_cost(self, user_id: str, session_id: str, cost_usd: float) -> None: ...
    def update_session_stats(self, user_id: str, session_id: str, message_count: int, cost_usd: float) -> None: ...
```

**All methods that currently accept only `session_id` must be updated to also require `user_id`.**

---

## Layer 3: Workspace Isolation

### User-Level Workspace Isolation

Each user gets a dedicated workspace directory at `data/users/{user_id}/workspace/`. The agent SDK subprocess runs with `cwd` set to this directory. All tool execution (Write, Bash, Read) is constrained to this workspace via hooks and permission callbacks. Sessions within the same user share the workspace — cross-session isolation for a single user is handled at the session management layer (Layer 2), not the filesystem layer.

### Directory Layout

```
data/users/{user_id}/
├── workspace/                          # User workspace (agent cwd)
│   ├── .claude/skills/                 # Skills
│   ├── uploads/                        # User-uploaded files
│   └── outputs/                        # Agent-generated files
├── claude-data/                        # CLI-native session data
│   ├── sessions/                       # JSONL session files
│   └── settings.json                   # Per-user agent settings
└── memory/                             # L2 agent memory (Markdown)
    ├── MEMORY.md
    └── *.md
```

### Enforcement Mechanisms

#### 1. Tool-Level Hooks (PreToolUse)

Before any `Write` or `Bash` tool executes, hooks rewrite paths and deny out-of-bounds access:

```python
# Write hook: rewrite absolute paths to workspace-relative
def pre_write_hook(tool_input, user_workspace):
    file_path = Path(tool_input.get("file_path", ""))
    if file_path.is_absolute():
        # Rewrite to workspace-relative
        tool_input["file_path"] = str(user_workspace / file_path.name)
    # Deny writes outside workspace
    resolved = (user_workspace / tool_input["file_path"]).resolve()
    if not str(resolved).startswith(str(user_workspace.resolve())):
        raise PermissionError("Write outside workspace denied")

# Bash hook: rewrite output redirections
def pre_bash_hook(command, user_workspace):
    # Scan for > and >> targets, rewrite to workspace
    ...
```

#### 2. Tool Permission Callback (can_use_tool)

The SDK callback denies operations targeting paths outside the user's workspace:

```python
def can_use_tool(tool_name, tool_input, user_workspace):
    if tool_name == "Write":
        target = Path(tool_input["file_path"])
        if not str(target.resolve()).startswith(str(user_workspace.resolve())):
            return False
    if tool_name == "Bash":
        # Deny commands that write outside workspace
        if contains_external_write(tool_input["command"], user_workspace):
            return False
    return True
```

#### 3. Post-Task File Relocation

After each agent task, newly created or modified files are relocated to `outputs/`. Each file is stored with a unique `stored_name` (format: `{uuid_short}_{sanitized_original}`) to prevent cross-session and cross-task collisions. The original filename and unique stored_name are both recorded in the `generated_files` database table.

```python
# Per-task scan and relocation (outputs/ + workspace root + leaked paths)
# Stored name ensures session A's chart.png never overwrites session B's chart.png
def generate_stored_name(original_name: str) -> str:
    uuid_short = uuid.uuid4().hex[:8]
    safe_name = sanitize_filename(original_name)
    return f"{uuid_short}_{safe_name}"

def relocate_to_outputs(file_path: Path, outputs_dir: Path) -> dict:
    stored_name = generate_stored_name(file_path.name)
    dest = outputs_dir / stored_name
    shutil.move(str(file_path), str(dest))
    return {
        "filename": file_path.name,       # original, for UI display
        "stored_name": stored_name,        # unique, for disk
        "size": dest.stat().st_size,
        "url": f"/api/users/{{user_id}}/download/outputs/{stored_name}",
        "generated_at": datetime.fromtimestamp(dest.stat().st_mtime, tz=timezone.utc).isoformat(),
    }

# Leaked files (home dir, /tmp, server CWD) are relocated the same way
KNOWN_LEAK_PATHS = [
    Path.home(),
    Path("/tmp"),
    Path.cwd(),
]

def relocate_leaked_files(user_workspace):
    outputs = user_workspace / "outputs"
    for leak_path in KNOWN_LEAK_PATHS:
        for f in recently_created_files(leak_path):
            relocate_to_outputs(f, outputs)
```

The `seen_filenames` set deduplicates within a single task. Unique `stored_name` handles cross-task and cross-session deduplication.

#### 4. Agent Subprocess cwd

When spawning the agent SDK subprocess, the working directory is set to the user's workspace:

```python
workspace = user_workspace_dir(user_id)

options = ClaudeAgentOptions(
    cwd=str(workspace),  # ← constrains all relative path operations to user workspace
    ...
)
```

Skills are synced to the workspace `.claude/skills/` directory.

### Skill Execution Security Boundary

Skills are **not executable code** — they are Markdown instructions injected into the Agent's system prompt. The Agent interprets skill instructions and carries them out via its tools (Bash, Write, WebFetch, etc.). This means a skill can cause the Agent to do anything the Agent is normally permitted to do.

**Trust model**: Uploading a private skill = injecting unverified instructions into your own Agent. The boundary is:
- **User-to-user isolation** via containers — skill-induced actions in user A's container cannot reach user B's files.
- **Shared skills are read-only** — mounted `ro` in containers, preventing cross-user tampering.
- **User responsibility** — the user bears the risk of what their private skills instruct the Agent to do.

**Current protections** (container mode):

| Risk | Protection |
|------|-----------|
| Cross-user filesystem access | Container isolation (per-user) |
| Shared skill tampering | Read-only mount |
| Host system compromise | Non-root user (UID 1000), CPU/memory limits |
| Resource exhaustion | 4 GB memory, 1 CPU core per container |

**Known gaps** (accepted for Phase 1):

| Gap | Rationale |
|-----|-----------|
| Agent can access external network | Not restricted; skill could exfiltrate the user's own data to an external endpoint |
| Agent can `pip install` arbitrary packages | No package whitelist; skill could instruct Agent to install and run malicious code |
| No skill content review | Private skills are not scanned or approved before use |
| Agent runs with `bypassPermissions` | No tool-use approval prompts inside the container; the Agent acts autonomously on skill instructions |

These gaps are acceptable for internal/trusted-user deployments where the primary threat is accidental cross-user leakage (addressed by container isolation), not malicious self-harm. A malicious skill can only harm the user who uploaded it.

**Future hardening** (Phase 3):
- Network egress whitelist per container (allowlist of approved external domains)
- Package installation restrictions (pre-approved package index or proxy)
- Optional skill review/approval gate for shared skill promotion

---

## Layer 4: Message Buffer Isolation

### Architecture

```
                    ┌──────────────────────┐
                    │    MessageBuffer      │
                    │                      │
                    │  sessions: {          │
                    │    session_id → {     │
                    │      user_id: "...",  │  ← NEW: user_id stored in buffer entry
                    │      messages: [...], │
                    │      subscribers: []  │
                    │    }                  │
                    │  }                    │
                    └──────────────────────┘
                           │
                           │ subscribe(session_id, user_id)
                           ▼
                    ┌──────────────────────┐
                    │   WebSocket Handler   │
                    │                      │
                    │  Verify:              │
                    │  ws.user_id ==        │
                    │  session.user_id      │
                    └──────────────────────┘
```

### Enforcement

Each buffer entry stores the owning `user_id`, set at session **creation** time (not on first message). All public methods accept a `user_id` parameter and verify ownership before accessing the buffer:

```python
class MessageBuffer:
    def _ensure_buf(self, session_id: str, user_id: str | None = None):
        """Lazy-init buffer. On new buffers, stores user_id. On existing
        buffers, verifies the caller's user_id matches the stored owner."""
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "messages": [],
                "consumers": set(),
                "done": False,
                "state": "idle",
                "cost_usd": 0.0,
                "last_active": time.time(),
                "user_id": user_id,  # ← set at creation, never changes
            }
        else:
            buf = self.sessions[session_id]
            stored = buf.get("user_id")
            if user_id is not None and stored is not None and stored != user_id:
                raise PermissionError(
                    f"Session {session_id} belongs to user {stored}, "
                    f"not {user_id}"
                )
        return self.sessions[session_id]

    def add_message(self, session_id: str, message: dict, user_id: str | None = None):
        buf = self._ensure_buf(session_id, user_id=user_id)
        buf["messages"].append(message)

    def subscribe(self, session_id: str, user_id: str | None = None):
        buf = self._ensure_buf(session_id, user_id=user_id)
        ...
```

All public methods (`add_message`, `get_history`, `subscribe`, `cancel`, `mark_done`, `get_session_state`, `get_state`, `is_done`, `unsubscribe`, `remove_session`) accept an optional `user_id` parameter. When `ENFORCE_AUTH=false`, `user_id=None` skips the ownership check (backward compatibility). When `ENFORCE_AUTH=true`, `user_id` is **required** — passing `None` raises `ValueError`. This prevents silent permission bypass if a call site forgets to pass `user_id` in production.

### WebSocket Authentication

When `ENFORCE_AUTH=true`:
- JWT verified at connection time via `?token=<jwt>` query parameter
- `user_id` locked to the token's verified identity for the entire connection lifetime
- Any incoming message whose `data.user_id` field doesn't match the locked identity is rejected with an error frame sent back, then skipped
- `user_id` cannot change mid-connection — set once from the token, never overwritten

When `ENFORCE_AUTH=false` (development only):

**Connection-level user_id determination** (priority order):
1. `?token=<jwt>` — if query param contains a valid JWT, use `sub` claim (supports semi-authenticated dev testing)
2. `?user_id=xxx` — if no valid token, use explicit query param
3. Neither → default `"default"`

**First-message binding**:
- If the first WebSocket JSON message carries a `user_id` field and the current value is `"default"`, upgrade to the message's `user_id` (one-time binding). If the current value is already non-`"default"` and differs, reject with an error frame.
- If the message carries no `user_id` field, keep the current value.

**Lock timing**: After the first message is processed, `user_id` is permanently locked. Any subsequent message whose `data.user_id` differs from the locked value is rejected with an error frame.

Key invariants:
- The connection always has a non-None `user_id` — never `None` or `""`
- `"default"` acts as an "unbound" sentinel, allowing exactly one upgrade
- After locking, behavior is identical to `ENFORCE_AUTH=true` mode

---

## Layer 5: Memory Isolation

### L1 Memory (Platform — SQLite)

```sql
CREATE TABLE user_memory (
    user_id TEXT PRIMARY KEY,
    memory_data TEXT,    -- JSON blob: preferences, entities, audit context
    updated_at TIMESTAMP
);
```

The `MemoryManager` constructor takes `user_id` and all `read()` / `update()` / `replace()` operations filter by `WHERE user_id = ?`.

```python
class MemoryManager:
    def __init__(self, user_id: str):
        self.user_id = user_id

    def read(self) -> dict:
        row = db.execute(
            "SELECT memory_data FROM user_memory WHERE user_id = ?",
            (self.user_id,)
        ).fetchone()
        return json.loads(row[0]) if row else {}

    def update(self, updates: dict) -> dict:
        current = self.read()
        merged = {**current, **updates}  # Immutable merge
        db.execute(
            "INSERT OR REPLACE INTO user_memory (user_id, memory_data) VALUES (?, ?)",
            (self.user_id, json.dumps(merged))
        )
        return merged
```

### L2 Memory (Agent — Markdown Files)

Each user's agent memory is stored in `data/users/{user_id}/memory/`:

```
data/users/{user_id}/memory/
├── MEMORY.md              # Index of all memory entries
├── user_role.md           # User profile & preferences
├── project_context.md     # Ongoing project context
└── feedback_*.md          # User feedback records
```

The agent SDK can only read/write within the user's workspace, so L2 memory is naturally isolated through workspace isolation (Layer 3).

### L1 / L2 Memory Status

#### L1 Memory (Platform Memory)

**Storage**: SQLite `user_memory` table, four fields — `preferences`, `entity_memory`, `audit_context`, `file_memory`.

**Write path**: `PUT /api/users/{user_id}/memory` → `MemoryManager.update()` → `INSERT OR REPLACE INTO user_memory`. Fully manual — triggered only by API call. No UI entry point exists (the `MemoryPanel` component is implemented but not imported by `App.tsx`).

**Read path**: `load_memory()` → `MemoryManager.read()` → injected into system prompt as "## Memory Context" on every conversation turn. Agent reads L1 content automatically.

**Summary**: L1 is wired end-to-end for reading, but there is no UI for users to write or edit L1 data. The write endpoint exists server-side but has no corresponding frontend entry point.

**Planned UI entry**: Add a "Memory" button in the `UserMenu` dropdown (user avatar menu), positioned above "Logout". The MemoryPanel component (already implemented in `frontend/src/components/MemoryPanel.tsx`) will be rendered as a full-page overlay when this button is clicked, following the same pattern as FeedbackPage, EvolutionPanel, and MCPPage.

#### L2 Memory (Agent Memory)

**Storage**: Markdown files in `data/users/{user_id}/memory/`.

**Write path**: API endpoints exist (`PUT/DELETE /api/users/{user_id}/memory/agent-notes/*`) and `MemoryPanel` has a UI for it (Agent Notes tab with Save button), but the component is not loaded in production.

**Read path (BROKEN)**: `load_agent_memory_for_prompt()` is implemented in `MemoryManager` but **never called** by `build_system_prompt()` or any other code path. The Agent never sees L2 memory content.

**Agent write path (BLOCKED)**: The agent's `cwd` is `data/users/{user_id}/workspace/`. Accessing `../memory/` resolves to `data/users/{user_id}/memory/`, which is outside the workspace subtree. `is_path_within_workspace()` rejects it. Both Agent-initiated reads and writes to L2 memory are blocked by Layer 3 enforcement.

**Summary**: L2 data layer is 100% implemented but business layer is not connected. Agent cannot read or write L2 memory. The feature is effectively inactive pending wiring.

### API Endpoint Enforcement

All memory endpoints accept `{user_id}` in the path and MUST verify it matches the authenticated user:

```python
@router.get("/api/users/{user_id}/memory")
async def get_memory(
    user_id: str,
    current_user: str = Depends(get_current_user),
):
    require_user_match(user_id, current_user)
    mgr = MemoryManager(user_id=user_id)
    return mgr.read()
```

---

## Layer 6: Database Isolation

### Column Naming Convention

The existing codebase uses `id` as the primary key column name for both `users` and `sessions` tables. This document uses `user_id` and `session_id` for clarity.

**Phase 1 migration will rename these columns:**
- `users.id` → `users.user_id`
- `sessions.id` → `sessions.session_id`

All FK references and indexes must be updated accordingly. This is a one-time breaking schema change applied in Step 1.

### Schema Design

All user-scoped tables include `user_id` as a foreign key:

```sql
CREATE TABLE users (
    user_id       TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL DEFAULT '',     -- bcrypt hash, empty = no password set
    role          TEXT NOT NULL DEFAULT 'user', -- 'user' or 'admin'
    status        TEXT NOT NULL DEFAULT 'active', -- 'active' or 'disabled'
    disabled_at   REAL,                         -- timestamp when disabled
    disabled_by   TEXT,                         -- admin user_id who performed the action
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(user_id),
    ...
);

CREATE TABLE user_memory (
    user_id TEXT PRIMARY KEY REFERENCES users(user_id),
    ...
);

CREATE TABLE uploads (
    id          TEXT PRIMARY KEY,                       -- UUID
    user_id     TEXT NOT NULL REFERENCES users(user_id),
    session_id  TEXT NOT NULL REFERENCES sessions(session_id),
    filename    TEXT NOT NULL,                          -- 用户上传时的原始文件名, 如 report.pdf
    stored_name TEXT NOT NULL,                          -- 磁盘上的唯一文件名, 如 a3f2c91b_report.pdf
    file_size   INTEGER NOT NULL DEFAULT 0,
    mime_type   TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',               -- 下载路径
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_uploads_user ON uploads(user_id, created_at DESC);
CREATE INDEX idx_uploads_session ON uploads(session_id);

CREATE TABLE generated_files (
    id          TEXT PRIMARY KEY,                       -- UUID
    user_id     TEXT NOT NULL REFERENCES users(user_id),
    session_id  TEXT NOT NULL REFERENCES sessions(session_id),
    filename    TEXT NOT NULL,                          -- Agent 生成时的文件名, 如 chart.png
    stored_name TEXT NOT NULL,                          -- 磁盘上的唯一文件名
    file_size   INTEGER NOT NULL DEFAULT 0,
    mime_type   TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',               -- 下载路径
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_generated_files_user ON generated_files(user_id, created_at DESC);
CREATE INDEX idx_generated_files_session ON generated_files(session_id);
```

### File Download Authorization

Download endpoints verify both path-level and record-level ownership:

**Endpoints:**
- `GET /api/users/{user_id}/download/uploads/{stored_name}` — uploaded files
- `GET /api/users/{user_id}/download/outputs/{stored_name}` — agent-generated files

**Two-layer verification:**

1. **Path parameter check** — `verify_path_user(user_id, current_user)` ensures the URL's `{user_id}` matches the authenticated user (standard Layer 1 enforcement).

2. **Record ownership check** — Query the `uploads` or `generated_files` table by `stored_name` and verify `row["user_id"] == current_user`. Return 404 (not 403) on mismatch to avoid leaking file existence. This prevents a user from guessing another user's `stored_name` (which contains a uuid prefix but is not cryptographically secret).

```python
@router.get("/api/users/{user_id}/download/uploads/{stored_name}")
async def download_upload(
    user_id: str,
    stored_name: str,
    current_user: str = Depends(get_current_user),
):
    verify_path_user(user_id, current_user)
    row = db.execute(
        "SELECT user_id, stored_name, filename, file_size FROM uploads WHERE stored_name = ?",
        (stored_name,)
    ).fetchone()
    if not row or row["user_id"] != current_user:
        raise HTTPException(status_code=404, detail="File not found")
    file_path = user_workspace_dir(user_id) / "uploads" / stored_name
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, filename=row["filename"])
```

### Audit Log (SQL Table)

All audit log entries are written to the SQLite `audit_log` table:

```sql
CREATE TABLE audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    category   TEXT NOT NULL,       -- 'auth', 'session', 'skills', 'mcp', 'files', 'admin', 'resource'
    user_id    TEXT,                -- No FK constraint: audit records must survive user disable
    action     TEXT,                -- 'session.create', 'session.delete', 'token.create', etc.
    data       TEXT NOT NULL DEFAULT '{}',  -- JSON metadata
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX idx_audit_log_category ON audit_log(category, created_at DESC);
CREATE INDEX idx_audit_log_user ON audit_log(user_id, created_at DESC);
```

```python
class AuditLogger:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def log(self, category: str, data: dict[str, Any]) -> None:
        """Append an audit log entry to the SQL database."""
        async with self.db.connection() as conn:
            await conn.execute(
                "INSERT INTO audit_log (category, user_id, action, data, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    category,
                    data.get("user_id"),
                    data.get("action"),
                    json.dumps(data, ensure_ascii=False),
                    time.time(),
                ),
            )
            await conn.commit()

    async def query(
        self,
        category: str,
        *,
        user_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query audit log entries from SQL."""
        query = "SELECT id, category, user_id, action, data, created_at FROM audit_log WHERE category = ?"
        params: list[Any] = [category]

        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if action:
            query += " AND action = ?"
            params.append(action)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        async with self.db.connection() as conn:
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()

        return [
            {"id": row[0], "category": row[1], "user_id": row[2], "action": row[3],
             **json.loads(row[4]), "created_at": row[5]}
            for row in rows
        ]
```

All state-changing operations (create, delete, update) MUST write to the audit log.

### Auto-Created Users

When a session is created and the user doesn't exist, `SessionStore.create_session()` inserts:
```sql
INSERT OR IGNORE INTO users (id, password_hash, role, created_at, last_active_at)
VALUES (?, '', 'user', ?, ?)
```

The empty `password_hash` means auto-created users can never authenticate via password. They can only use the system in `ENFORCE_AUTH=false` mode. To enable auth, use `POST /api/auth/register` to set a password.

### Query Discipline

Every query against user-scoped tables MUST include `WHERE user_id = ?`:

```python
# CORRECT
db.execute("SELECT * FROM sessions WHERE session_id = ? AND user_id = ?",
           (session_id, user_id))

# WRONG — allows cross-user access
db.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
```

---

## Session ID Format Validation

All operations accepting a `session_id` MUST validate the format before any DB query:

```python
import re

SESSION_ID_PATTERN = re.compile(r'^session_[a-zA-Z0-9_.-]+_\d+\.\d+_[a-f0-9]+$')

def validate_session_id(session_id: str) -> None:
    if not SESSION_ID_PATTERN.match(session_id):
        raise HTTPException(status_code=400, detail=f"Invalid session ID: {session_id!r}")
```

**Format**: `session_{user_id}_{timestamp}_{uuid_hex}`

This is called as the first line in `SessionStore` methods: `get_session_history`, `delete_session`, `add_message`, `update_session_title`, `update_session_status`, `update_session_cost`, `update_session_stats`. It catches malformed or injection-attempt session IDs before they reach the database.

The user_id portion of the session ID allows alphanumeric characters, dots, underscores, and hyphens.

---

## Layer 7: Container Isolation (Required for Production)

Production environments MUST enable `CONTAINER_MODE=true`. Each user runs in an isolated Docker container:

```
┌──────────────────────────────────────────┐
│            Docker Host                     │
│                                           │
│  ┌─────────────────────┐                  │
│  │ Container: user_a    │                  │
│  │  Memory: 4GB         │                  │
│  │  CPU: 1.0            │                  │
│  │  Volumes:            │                  │
│  │   workspace/: rw     │                  │
│  │   skills/: ro        │                  │
│  │   claude-data/: rw   │                  │
│  └─────────────────────┘                  │
│                                           │
│  ┌─────────────────────┐                  │
│  │ Container: user_b    │                  │
│  │  Memory: 4GB         │                  │
│  │  CPU: 1.0            │                  │
│  │  Volumes:            │                  │
│  │   workspace/: rw     │                  │
│  │   skills/: ro        │                  │
│  │   claude-data/: rw   │                  │
│  └─────────────────────┘                  │
└──────────────────────────────────────────┘
```

Container mode provides hardware-level isolation as the final defense layer. Even if tool hook enforcement in Layer 3 has a bypass, the container boundary prevents cross-user filesystem access and resource contention.

Development environments may skip containers (`CONTAINER_MODE=false`) for faster iteration, but production MUST enable them.

### Container Lifecycle

| Event | Action |
|-------|--------|
| First request from user | Create container (`web-agent-{user_id}`) |
| User idle > TTL | Stop container (preserve volumes) |
| User returns | Restart container |
| User deleted | Remove container and volumes |

---

## Cross-Cutting: Admin Separation

### Role Model

| Role | Permissions |
|------|------------|
| `user` | Access own sessions, workspace, memory |
| `admin` | List all users, view any session (audit), manage MCP servers, manage containers |

### Admin Authentication

Admin endpoints are prefixed with `/api/admin/` and require `role=admin` in the JWT claims:

```python
def require_admin(token: str = Depends(verify_token)) -> str:
    """Extract user_id from token, verify admin role from claims (no DB hit)."""
    payload = decode_token(token)
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return payload["sub"]
```

### Admin Audit

Admin access to other users' data is logged with both the admin's user_id and the target user_id:

```python
await audit_logger.log("admin", {
    "user_id": admin_user_id,
    "action": "admin.session.view",
    "target": target_session_id,
    "target_user": target_user_id,
})
```

---

## Shared Resources

Some resources are intentionally shared across users:

| Resource | Path | Access | Reason |
|----------|------|--------|--------|
| Shared skills | `data/shared-skills/` | Read-only, all users | Common agent capabilities |
| CLI session map | `data/cli_sessions.json` | Read/write, all users | Session resume across restarts |

Shared resources must never contain user-specific data. The CLI session map is keyed by web-agent session_id (which already encodes user_id), so cross-user collision is impossible by design. A header comment in the file enforces this constraint:

```python
# cli_sessions.json — SHARED ACROSS ALL USERS
# Keys: web-agent session_id → CLI UUID
# DO NOT add user-specific data to this file. It is the only cross-user
# shared state file and must remain safe to read by any user.
```

---

## Enforcement Checklist

### API Endpoint Checklist

- [ ] Every `{user_id}` path parameter is verified via `verify_path_user()` (35 endpoints)
- [ ] Every `SessionStore` method that queries by `session_id` also filters by `user_id`
- [ ] Every `MessageBuffer` operation verifies `user_id` ownership
- [ ] WebSocket `user_id` is locked per connection, rejected if mismatched mid-connection
- [ ] Admin endpoints verify `role=admin` in JWT claims
- [ ] All state-changing operations write to SQL `audit_log`
- [ ] Session ID format validated on all operations (`session_{user_id}_*` pattern)

### Tool Execution Checklist

- [ ] `Write` hook rewrites paths to user workspace
- [ ] `Bash` hook rewrites output redirections to user workspace
- [ ] `can_use_tool` denies writes outside user workspace
- [ ] Post-task file relocation captures leaked files
- [ ] Agent subprocess `cwd` is set to user workspace
- [ ] User skill code runs with same constraints as Agent tools (container isolation, non-root, resource limits)
- [ ] Shared skills mounted read-only in user containers

### Data Directory Checklist

- [ ] `data/users/{user_id}/` is the only location for user data
- [ ] No user data in server CWD, `/tmp`, or home directory
- [ ] `data/` is in `.gitignore` and never committed
- [ ] File permissions restrict cross-user access at OS level (0700 on user dirs)

---

## Design Decisions

This section documents the rationale behind key design choices. These are constraints future maintainers should understand before modifying the isolation model.

### Why Embed `user_id` in Session ID Format

Session IDs follow the pattern `session_{user_id}_{timestamp}_{uuid_hex}`. This is deliberate:

1. **Fast-path ownership check** — The embedded `user_id` allows `validate_session_ownership()` to verify ownership with a regex match before hitting the database. This catches malformed or spoofed session IDs at the API boundary.
2. **Human readability** — Operators inspecting logs or database rows can immediately identify the owning user without a JOIN.
3. **Defense in depth** — Even if a query mistakenly omits `WHERE user_id = ?`, the session ID itself encodes the owner, making accidental cross-user access visible in audit trails.

Trade-off: Session IDs are not opaque. An attacker who knows a user_id can guess the session ID prefix. This is acceptable because the session ID is always used together with an authenticated user_id — knowledge of the format does not grant access.

### Why L2 Memory Is Separate from the Workspace

Agent memory (Markdown files) is stored at `data/users/{user_id}/memory/`, NOT under the `workspace/` directory. Rationale:

1. **Memory persists across sessions** — Agent memory should accumulate knowledge over time, separate from the workspace where agent tools operate.
2. **Workspace isolation already covers it** — The agent SDK's `cwd` is scoped to the user workspace, so L2 memory is naturally protected by Layer 3 enforcement (PreToolUse hooks, `can_use_tool`).
3. **Single source of truth** — Memory files live at a fixed path independent of which session is active. The agent accesses them via relative paths (`../memory/`) from the workspace directory. Controlled access is permitted through Layer 3 hooks.

### Why Dual-Method Token Extraction

`get_current_user` accepts tokens from both `Authorization: Bearer` header (primary) and `?token=` query param (fallback):

1. **REST endpoints** use the `Authorization` header — standard, secure, not logged in server access logs.
2. **WebSocket connections** use the `?token=` query param — the WebSocket upgrade handshake cannot carry custom headers in all browser implementations. The query param is a pragmatic fallback.

The query param method is only active when the `Authorization` header is absent. In production, WebSocket connections should use `wss://` so the token is protected by TLS encryption.

### Why Container Isolation as Final Defense Layer

Layer 7 (Container) provides hardware-level separation as the **last line of defense**, not the first:

1. **Layers 1-6 are software enforcement** — They rely on correct code in hooks, callbacks, and SQL queries. A single bug in any layer could create a bypass.
2. **Container boundary is enforced by the kernel** — Even if all software defenses fail, a process in container A cannot access files in container B. This is guaranteed by Linux namespaces and cgroups, not application code.
3. **Development vs. production asymmetry** — Development environments skip containers for fast iteration (tools run directly on the host). Production MUST enable them because the blast radius of a bypass is larger.

Container isolation is expensive (startup latency, resource overhead) and is not a substitute for correct software isolation. It is a safety net, not the primary mechanism.

### Why MessageBuffer Stores `user_id` at Creation Time

The `MessageBuffer` stores `user_id` in the buffer entry when the buffer is **first created** (first call to `_ensure_buf` for a given `session_id`), not on the first message:

1. **Prevents race-condition hijacking** — If user_id were set on first message, an attacker could race to send the first message on a newly created session and claim ownership.
2. **Aligns with session lifecycle** — The buffer is created by `subscribe()`, which is called during WebSocket connection setup when the user's identity is already verified. Storing user_id at that point ties the buffer to the authenticated connection.
3. **Immutable after creation** — Once set, the stored `user_id` never changes. All subsequent operations verify the caller's user_id against the stored value.

### Why `ENFORCE_AUTH=false` Returns `"default"`

In development mode, `get_current_user` returns `"default"` for all requests. This is a convenience, not a security gap:

1. **Development is single-user** — A developer running the server locally is the only user. Multi-user isolation is irrelevant.
2. **`user_id="default"` is a valid user** — Session creation auto-creates this user in the `users` table. All isolation machinery still runs; it just always sees the same user.
3. **Auth code path is still exercised** — The `require_user_match` / `verify_path_user` path is a no-op when `ENFORCE_AUTH=false`, but the function call is still present in the code. This means production auth enforcement is a configuration change, not a code change.

### Why Uploaded Files Use Auto-Generated Unique Names

Uploaded files are stored on disk with a unique `stored_name` (format: `{uuid_short}_{sanitized_original}`), while the original filename is preserved only in the database `filename` column:

1. **Prevents cross-session collisions** — Multiple sessions belonging to the same user share a workspace. Without unique names, session A uploading `report.pdf` would silently overwrite session B's `report.pdf` via `shutil.move`. Auto-generated names eliminate this class of conflict entirely.
2. **Preserves Agent file-type detection** — The `stored_name` retains the original extension (e.g., `a3f2c91b_report.pdf`), so the Agent can still infer file type from the filename. A pure UUID would break this.
3. **Separation of identity and storage** — The DB `filename` column holds the human-readable original name for UI display. `stored_name` is the disk identifier. `url` provides the download path. These three serve different consumers (user / filesystem / HTTP client) and should not be conflated.
4. **Defense against path traversal in filenames** — A malicious filename like `../../../etc/passwd` would be sanitized into `stored_name` before touching the filesystem. The original is stored as metadata only.

The same pattern applies to `generated_files`: Agent-created files use unique `stored_name` on disk, with the Agent-chosen filename in the `filename` column.

File deletion removes both the DB record and the disk file. `session_id` is NOT NULL on both tables — every file belongs to a specific session.

### Why Users Are Disabled Instead of Deleted

User records are **never deleted** from the database. There is no `DELETE /api/admin/users/{user_id}` endpoint. Instead, users are disabled via `PATCH /api/admin/users/{user_id}/disable`:

1. **Referential integrity** — Sessions, messages, uploads, generated_files, audit_log, and user_memory all reference `user_id` via foreign keys. Deleting a user would require either `ON DELETE CASCADE` (destroying audit history) or `ON DELETE SET NULL` (orphaning records and breaking query filters). Neither is acceptable.
2. **Audit trail preservation** — Every state-changing operation is logged with `user_id`. Deleting a user would create gaps in the audit log, making it impossible to reconstruct who performed what action during a security investigation.
3. **GDPR / data retention** — If a future compliance requirement mandates data deletion, the audit log entries can be anonymized (set `user_id` to a hash or `"deleted"`) without destroying the event record itself. This is a data-masking operation, not a row-deletion operation.
4. **Simplicity** — Active/disabled is a two-state model with clear semantics. Disabled users cannot authenticate and their running sessions are terminated. Re-enabling is a single-column update. No partial-delete or soft-delete-with-grace-period complexity.

Disabled users' data (sessions, files, messages, memory) is preserved. Containers are stopped but volumes are kept. Storage cost is the only ongoing cost of a disabled user.

---

## Migration Path

### Phase 1: Close Critical Gaps (Immediate)

Steps 4 and 5 are independent of Steps 2 and 3 — they can execute in parallel.

**Parallel track A:** Step 1 → Step 2 → Step 3 → Step 6a/6b → Step 7 → Step 9
**Parallel track B:** Step 1 → Step 4 (runs alongside 2+3)
**Parallel track C:** Step 5 (runs alongside 2+3+4)

Each step lists the target files and key line numbers.

---

**Step 1: Schema migration** — `src/database.py`

- Add `password_hash TEXT NOT NULL DEFAULT ''` and `role TEXT NOT NULL DEFAULT 'user'` columns to `users` table
- Add `status TEXT NOT NULL DEFAULT 'active'`, `disabled_at REAL`, `disabled_by TEXT` columns to `users` table for user lifecycle management (disable, never delete)
- Add `audit_log` table with indexes on `(category, created_at DESC)` and `(user_id, created_at DESC)` (after line 120)
- Add `uploads` table with columns: `id`, `user_id`, `session_id` (NOT NULL), `filename` (original name), `stored_name` (unique disk name, format `{uuid_short}_{sanitized_original}`), `file_size`, `mime_type`, `url`, `created_at` — plus indexes on `(user_id, created_at DESC)` and `(session_id)`
- Add `generated_files` table with same structure (Agent-created files use same unique-name pattern)
- Implement `stored_name` generation: `f"{uuid.uuid4().hex[:8]}_{sanitize_filename(original_name)}"` in a new helper `src/file_utils.py`
- Write retroactive `ALTER TABLE` migrations for existing databases (add as a new method on `Database`, e.g., `migrate_v2()`)

**Depends on:** Nothing — can be done first.

---

**Step 2: Password authentication** — `src/auth.py`, `main_server.py`, `pyproject.toml`

- Add `bcrypt` dependency: `uv add bcrypt`
- Implement `hash_password(password: str) -> str` and `verify_password(password: str, password_hash: str) -> bool` in `src/auth.py`
- Create `LoginRequest` model with both `user_id: str` and `password: str` fields
- Update `POST /api/auth/token` at `main_server.py:4098-4128`: query `password_hash` + `role` from `users` table, verify password with bcrypt, pass role to `create_token()`
- Add `POST /api/auth/register` endpoint in `main_server.py` (near line 4128): validate password length >= 8, hash with bcrypt, check for duplicate user_id (return 409), create token
- Update `SessionStore.create_session()` at `src/session_store.py:60-78`: `INSERT OR IGNORE` must include `password_hash=''` and `role='user'`

**Depends on:** Step 1 (schema must have `password_hash` and `role` columns).

---

**Step 3: Dual-method token extraction** — `src/auth.py`

- Update `src/auth.py:86-103` (`get_current_user`): add `authorization: str | None = Header(None)` parameter
- Check `Authorization: Bearer <token>` first (primary), then fall back to `?token=` query param
- Add `verify_path_user(path_user_id: str, current_user: str) -> str` function that delegates to `require_user_match`
- Re-export `verify_path_user` alongside existing exports

**Depends on:** Step 2 (should reuse `verify_token` which already exists at line 56).

---

**Step 4: SessionStore user_id hardening** — `src/session_store.py`

- Add `user_id: str` parameter to `get_session_history()` at line 104: change query from `WHERE m.session_id = ?` to `WHERE m.session_id = ? AND s.user_id = ?` with a JOIN
- Add `user_id: str` parameter to `delete_session()` at line 152: add `AND user_id = ?` to all DELETE/UPDATE queries (lines 156, 162-163, 165)
- Add `user_id: str` parameter to `add_message()` at line 227: add `AND s.user_id = ?` filter or pass-through check
- Add `validate_session_id(session_id: str) -> None` function with regex pattern `r'^session_[a-zA-Z0-9_.-]+_\d+\.\d+_[a-f0-9]+$'` — call as first line in all methods accepting `session_id`

**Depends on:** Step 1 (schema must be ready). Independent of Steps 2-3.

---

**Step 5: MessageBuffer user_id binding** — `src/message_buffer.py`

- Add `user_id: str | None = None` parameter to all 11 public methods (add_message:256, get_history:317, get_session_state:341, get_state:357, mark_done:361, is_done:375, cancel:378, subscribe:389, unsubscribe:401, remove_session:406)
- Store `user_id` in buffer entry dict at creation time (`_ensure_buf` line 57-68)
- Verify ownership in `_ensure_buf`: if both stored and passed `user_id` are non-None and differ, raise `PermissionError`
- When `ENFORCE_AUTH=true` and `user_id=None`: raise `ValueError` (prevents silent bypass if a call site forgets to pass user_id)

**Depends on:** Nothing — can be done independently. Runs in parallel with Steps 2+3+4.

---

**Step 6a: Wire auth to REST `{user_id}` endpoints** — `main_server.py`

- Add `current_user: str = Depends(get_current_user)` to all non-WebSocket endpoint function signatures with `{user_id}` in the URL path
- Call `verify_path_user(user_id, current_user)` as the first body line in each endpoint
- Pass `user_id` to `SessionStore` methods that now require it (get_session_history, delete_session, add_message)

**Depends on:** Steps 3, 4 (need `get_current_user` with Header support, `verify_path_user`, `SessionStore` user_id methods).

---

**Step 6b: Wire auth to WebSocket-connected endpoints** — `main_server.py`

- Add `current_user: str = Depends(get_current_user)` to remaining endpoint function signatures (those whose data flows through WebSocket)
- Call `verify_path_user(user_id, current_user)` as the first body line
- Pass `user_id` to `MessageBuffer` methods that now require it

**Depends on:** Steps 3, 5 (need `get_current_user` with Header support, `verify_path_user`, `MessageBuffer` user_id awareness).

---

**Step 7: Lock WebSocket user_id per connection** — `main_server.py`

**`ENFORCE_AUTH=true`:**
- Line 1716-1737 (WebSocket handler): lock `user_id` to verified token identity; reject any incoming message whose `data.user_id` doesn't match
- When message is rejected due to user_id mismatch, send an error frame back and skip processing

**`ENFORCE_AUTH=false`:**
- Connection-level user_id from: `?token=` → `?user_id=` → `"default"` (priority order)
- First WebSocket JSON message: if current value is `"default"` and message carries a different `user_id`, upgrade to it (one-time binding). If already non-`"default"` and message differs, reject with error frame.
- After first message processed, user_id is permanently locked. Any subsequent mismatch → error frame.

**Depends on:** Step 3 (need `verify_token` for auth mode validation).

---

**Step 8: SQL audit logging** — `src/audit_logger.py`, `src/database.py`

- Rewrite `AuditLogger` to use SQL: constructor takes `Database` instead of `base_dir: Path` (line 42)
- `log()` and `query()` methods operate on the `audit_log` SQL table created in Step 1
- Remove file-based JSONL hash chain code (lines 110-156)
- Add audit calls in `main_server.py` for: session create (line 2214), session delete (line 2340), memory update (line 2904), file upload (line 2430), file delete (line 2493)

**Depends on:** Step 1 (audit_log table must exist).

---

**Step 9: Workspace isolation hardening + L2 memory wiring** — `main_server.py`

- Add `user_workspace_dir(user_id: str) -> Path` helper function that returns `data/users/{user_id}/workspace/`
- Ensure `build_sdk_options()` at line 983 sets `cwd=str(user_workspace_dir(user_id))`
- Ensure PreToolUse hooks (lines 912-957) enforce path constraints to user workspace
- Ensure post-task file relocation (lines 1464-1554) targets user workspace
- **L2 memory wiring**: Add `load_agent_memory_for_prompt()` call to `build_system_prompt()` so Agent can read L2 memory content
- **Workspace whitelist**: Add `../memory/` path exception in `is_path_within_workspace()` / `can_use_tool` to allow Agent reads and writes to `data/users/{user_id}/memory/` via relative path `../memory/` from the workspace. Without this exception, Layer 3 enforcement blocks all Agent access to L2 memory.

**Depends on:** Steps 4, 6a (need session-aware `SessionStore` and auth wiring).

---

**Step 10: Admin hardening** — `src/admin_auth.py`

- Replace no-op `require_admin()` at line 11-13: accept JWT token (or `Request`), decode it, verify `role == "admin"` in claims, raise 403 if not admin
- Add `admin_required: str = Depends(require_admin)` to admin endpoint signatures
- Add audit logging for admin access to other users' data (log both admin user_id and target user_id)

**Depends on:** Steps 2, 3 (need JWT with role claims and working token verification).

### Phase 2: Hardening

1. ~~Add rate limiting per user~~ — Deferred. Not required for current internal deployment. Container CPU/memory limits already prevent single-user resource monopolization.
2. Set file permissions (0700) on `data/users/{user_id}/` directories
3. Add comment guard on `cli_sessions.json` — must never contain user-specific data
4. Remove `destroy_with_volumes()` from Phase 2 since user deletion is not supported (see User Lifecycle). Container cleanup on disable is handled in Phase 1.

### Phase 3: Production Readiness

1. Enforce `CONTAINER_MODE=true` as non-configurable in production
2. Integrate OAuth 2.0 / OIDC identity provider
3. Add Prometheus metrics per user (requests, errors, latency, cost)
4. Implement user quota management (sessions, tokens, storage)

---

## Appendix: Implementation Status

Status legend: ✅ Implemented ⚠️ Partial / weak ❌ Missing

### Layer 1: Identity & Authentication

| Feature | Status | Location |
|---------|--------|----------|
| `ENFORCE_AUTH` env var | ✅ | `src/auth.py:17` |
| `create_token()` with sub/role/iat/exp | ✅ | `src/auth.py:33-53` |
| `verify_token()` | ✅ | `src/auth.py:56-83` |
| `require_user_match()` | ✅ | `src/auth.py:106-119` |
| `get_current_user` — Bearer header support | ❌ | `src/auth.py:86` — only Query param |
| `verify_path_user()` wrapper | ❌ | Does not exist |
| `hash_password()` / `verify_password()` | ❌ | Not implemented; bcrypt not installed |
| `POST /api/auth/token` requires password | ❌ | `main_server.py:4098` — only `user_id` field |
| `POST /api/auth/register` | ❌ | Endpoint does not exist |

### Layer 2: Session Isolation

| Feature | Status | Location |
|---------|--------|----------|
| `create_session(user_id, session_id)` | ✅ | `src/session_store.py:60` |
| `list_sessions(user_id)` filters by user_id | ✅ | `src/session_store.py:80-85` |
| `update_session_title` filters by user_id | ✅ | `src/session_store.py:168-175` |
| `update_session_status` filters by user_id | ✅ | `src/session_store.py:182-190` |
| `update_session_cost` filters by user_id | ✅ | `src/session_store.py:197-205` |
| `update_session_stats` filters by user_id | ✅ | `src/session_store.py:212-220` |
| `get_session_history` filters by user_id | ❌ | `src/session_store.py:104` — only `WHERE session_id = ?` |
| `delete_session` filters by user_id | ❌ | `src/session_store.py:152` — only `WHERE id = ?` |
| `add_message` has user_id param | ❌ | `src/session_store.py:227` — no user_id |
| Session ID format validation | ❌ | No `validate_session_id()` exists |
| Auth wired to 35 `{user_id}` endpoints | ❌ | No `Depends(get_current_user)` in any endpoint |

### Layer 3: Workspace Isolation

| Feature | Status | Location |
|---------|--------|----------|
| PreToolUse Write hook (path rewriting) | ✅ | `main_server.py:912-934` |
| PreToolUse Bash hook (redirect rewriting) | ✅ | `main_server.py:936-957` |
| `can_use_tool` callback (Write deny) | ✅ | `main_server.py:1279-1303` |
| Post-task file relocation (leaked files) | ✅ | `main_server.py:1464-1554` |
| Agent subprocess cwd set to workspace | ✅ | `main_server.py:983` — set to user workspace |
| `user_workspace_dir()` helper | ⚠️ | Inline path construction; should be a dedicated helper |
| PreToolUse hooks enforce workspace boundary | ✅ | `main_server.py:912-957` |
| Personal skills storage: `workspace/.claude/skills/` | ✅ | `main_server.py:2689-2702` |
| Shared skills mounted read-only in containers | ✅ | `container_manager.py:65` |
| Skill code execution via Agent tools (not direct exec) | ✅ | See Skill Execution Security Boundary |

### Layer 4: Message Buffer Isolation

| Feature | Status | Location |
|---------|--------|----------|
| Buffer stores `user_id` at creation | ❌ | `src/message_buffer.py:57-68` — no user_id in buffer dict |
| `_ensure_buf` ownership verification | ❌ | `src/message_buffer.py:57` — no user_id param |
| 11 public methods accept user_id | ❌ | All methods in `src/message_buffer.py` — no user_id param |
| WebSocket token from query param | ✅ | `main_server.py:1721-1731` |
| WebSocket user_id locked (auth mode) | ⚠️ | `main_server.py:1751` — reassigned, no explicit mismatch rejection |
| WebSocket user_id locked (non-auth mode) | ❌ | `main_server.py:1754` — reassigned on every message |

### Layer 5: Memory Isolation

| Feature | Status | Location |
|---------|--------|----------|
| MemoryManager(user_id) constructor | ✅ | `src/memory.py:58-67` |
| L1 read() filters by user_id | ✅ | `src/memory.py:78` |
| L1 update() scoped to user_id | ✅ | `src/memory.py:115-119` |
| L1 replace() scoped to user_id | ✅ | `src/memory.py:150-155` |
| L2 agent notes scoped to user dir | ✅ | `src/memory.py:65` (`self.user_dir`) |
| Memory endpoints verify user_id | ❌ | `main_server.py:2882-2939` — no `verify_path_user` |

### Layer 6: Database Isolation

| Feature | Status | Location |
|---------|--------|----------|
| `users` table exists | ✅ | `src/database.py:27-31` |
| `users.password_hash` column | ❌ | Missing from schema |
| `users.role` column | ❌ | Missing from schema |
| `users.status` / `disabled_at` / `disabled_by` columns | ❌ | Missing from schema |
| User disable / enable admin endpoints | ❌ | Not implemented |
| `sessions` table with user_id FK | ✅ | `src/database.py:34-43` |
| `messages` table with session_id FK | ✅ | `src/database.py:49-60` |
| `user_memory` table | ✅ | `src/database.py:65-72` |
| `uploads` table | ❌ | Not in schema; filesystem-only tracking |
| `generated_files` table | ❌ | Not in schema; current scan writes to in-memory list only |
| `audit_log` table | ❌ | Not in schema; AuditLogger is file-based |
| Query discipline (all queries filter by user_id) | ⚠️ | 3 methods missing user_id filter (see Layer 2) |

### Layer 7: Container Isolation

| Feature | Status | Location |
|---------|--------|----------|
| `CONTAINER_MODE` env var | ✅ | `main_server.py:208` |
| Container manager (create/pause/destroy) | ✅ | `src/container_manager.py:148-215` |
| Per-user volume isolation | ✅ | `src/container_manager.py:58` |
| Container lifecycle endpoints | ✅ | `main_server.py:4148-4197` |
| Container destroy cleans volumes | ❌ | Only removes container, not volumes; `destroy_with_volumes()` planned in Phase 2 |
| `CONTAINER_MODE` enforced in production | ❌ | Currently configurable; should be non-configurable |

### Cross-Cutting: Admin

| Feature | Status | Location |
|---------|--------|----------|
| Admin endpoints (`/api/admin/`) | ✅ | 14 endpoints in `main_server.py:3097-4238` |
| `require_admin()` role check | ❌ | `src/admin_auth.py:11-13` — no-op passthrough |
| Admin audit logging | ❌ | No admin access to other users' data is logged |


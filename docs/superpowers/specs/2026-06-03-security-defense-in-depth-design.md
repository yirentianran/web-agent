# Security Defense-in-Depth Design

## Context

Web Agent 是面向注册用户的半公开服务。威胁模型覆盖两类攻击者：
- **普通用户越权** — 用户 A 访问用户 B 的数据、session、文件
- **恶意用户主动攻击** — 注册用户尝试提权、容器逃逸、探测内部系统

## Architecture: Three Lines of Defense

每层假定外层可能被突破，独立提供防护。

### Layer 1 — Request Entry (Web Application Security)

| Measure | Purpose | Priority |
|---------|---------|----------|
| JWT → `httpOnly` cookie + CSRF token | Prevent XSS token theft, CSRF | P0 |
| API-wide rate limiting | Prevent brute force, malicious high-frequency requests | P0 |
| Input validation (schema validation for all user input) | Prevent injection, malformed data | P2 |
| CSP + security response headers (HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy) | Prevent XSS, MIME sniffing | P1 |
| Admin endpoints restricted to admin role | Access control for user management, dashboard, MCP config | P0 (already in place) |

### Layer 2 — Agent Behavior Control (Tool Call Security)

Already in place:

| Measure | Implementation |
|---------|---------------|
| Bash command filtering | `BashCommandFilter` — deny list (env, export, whoami, docker, etc.), env var expansion blocked except 10 safe vars |
| Sensitive file read blocking | `FileAccessFilter` — blocks .env, /proc/, .claude/, config files, certificates |
| Output sanitization | `OutputFilter` — redacts API keys, env var assignments, paths, ports; blocks entire lines |
| Tool disablement | `DISABLED_TOOLS = ("WebSearch", "WebFetch")` |
| Path rewriting for Write | Redirects external writes to `workspace/outputs/` |
| File size limit for Read | 20MB default |
| Tool result truncation | 50K chars default |

To add:

| Measure | Purpose | Priority |
|---------|---------|----------|
| **Tool call rate limiting** | Prevent malicious high-frequency tool calls from exhausting resources | P1 |
| **Network egress blocking** | Block agent from making HTTP requests via python/ruby/node (`urllib.request`, `requests.get`, `fetch`, `http.client`, `socket`) | P1 |
| **Write path traversal hardening** | Ensure ALL writes are confined to workspace, including edge cases (symlinks, relative paths like `../../../`) | P1 |
| **Audit logging** | Log all blocked operations for security review; structured log format with user_id, session_id, blocked_command, timestamp | P2 |

### Layer 3 — Infrastructure (Container / System Security)

| Measure | Purpose | Priority |
|---------|---------|----------|
| Container `--read-only` root filesystem (except workspace and temp dirs as tmpfs) | Prevent agent from modifying system files | P2 |
| Drop all Linux capabilities, no `--privileged` | Prevent container escape | P2 |
| Container network policy: deny inter-container communication | User A cannot reach user B's container | P2 |
| Data directory isolation by uid/gid per container | Prevent cross-user file access | P2 (already partially in place) |

## Implementation Phases

### Phase 1 (Immediate)

1. **CSRF + httpOnly cookie** — migrate JWT from localStorage to secure httpOnly cookie, add CSRF token
2. **Rate limiting** — add rate limiting middleware (e.g., slowapi on FastAPI)
3. **CSP + security headers** — add middleware to inject security headers on all responses
4. **Tool call rate limiting** — track per-session tool call count, reject after threshold
5. **Network egress blocking** — extend BashCommandFilter to block `urllib`, `requests`, `fetch`, `http.client`, `socket` in python/node/ruby one-liners
6. **Write path hardening** — normalize paths before rewriting, reject `../` escapes

### Phase 2 (Later)

7. **Audit logging** — structured JSON log for all blocked tool calls
8. **Container hardening** — read-only rootfs, drop capabilities, network isolation
9. **Input schema validation** — pydantic/FastAPI schema validation on all endpoints

## Current State vs Target

```
                Phase 0 (today)          Phase 1                     Phase 2
                ──────────────           ─────────                   ─────────
JWT storage     localStorage ──────────► httpOnly cookie
Rate limiting   none ──────────────────► API + tool-call
Security headers none ─────────────────► CSP + HSTS + ...
Network egress  partial (curl blocked)─► comprehensive (all HTTP)
Write paths     basic redirect ────────► hardened (symlink-safe)
Audit log       none ───────────────────────────────────────────────► structured JSON
Container       user isolation ─────────────────────────────────────► read-only + minimal caps
```

# Web Agent

Multi-user web agent platform — isolated sessions, real-time WebSocket streaming, persistent memory, and private workspaces.

**Stack**: FastAPI + React + SQLite + AI Agent SDK (Anthropic-compatible API)

## Quick Start

```bash
cp .env.example .env   # set ANTHROPIC_AUTH_TOKEN and MODEL
./setup.sh
./start-dev.sh
```

Backend at `http://127.0.0.1:8000`, frontend at `http://127.0.0.1:3000`. Open the frontend URL.

> **Windows**: Use WSL2 or Docker. Use `127.0.0.1` — `localhost` resolves IPv6-first and can break WebSocket.

## Core Features

- **Multi-user isolation** — independent sessions, files, workspace per user
- **Real-time streaming** — WebSocket chat with progressive text rendering and tool-call visualization
- **Session management** — create, rename, fork, cancel, delete; auto-generated titles; full message history
- **Sub-agent tasks** — TaskCreate / TaskUpdate lifecycle with status tracking
- **File workspace** — upload files as context, browse and download agent-generated outputs
- **Skill library** — upload skills (ZIP), share, install, rate, feedback
- **Evolution system** — automatic pattern discovery, wiki knowledge base, FTS5 semantic search
- **Container isolation** (optional) — per-user Docker containers with idle auto-stop and resource monitoring

### Auth & Security

- Password authentication with bcrypt + JWT (httpOnly cookies), opt-in via `ENFORCE_AUTH=true`
- CSRF protection via double-submit cookie pattern (`X-CSRF-Token` header)
- Admin role with dedicated dashboard, user management, MCP registry, and evolution monitoring
- Per-user data isolation — all sessions, files, skills scoped to authenticated user

### UX

- Dark / light theme toggle, system-aware default
- Internationalization — English and Chinese
- Stale-session auto-recovery

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_AUTH_TOKEN` | — | API key (required). Per-user override: `ANTHROPIC_AUTH_TOKEN_<USERID>` |
| `ANTHROPIC_BASE_URL` | — | Custom API endpoint (DeepSeek, Bailian, etc.) |
| `MODEL` | — | Main agent model (required, e.g. `claude-sonnet-4-6`) |
| `FLASH_MODEL` | `MODEL` | Lightweight tasks: title gen, instinct extraction |
| `PROD` | `false` | Serve frontend static files from backend |
| `ENFORCE_AUTH` | `false` | Require JWT auth for all endpoints |
| `JWT_SECRET` | auto | JWT signing secret (set in production) |
| `CONTAINER_MODE` | `false` | Enable per-user Docker container isolation |
| `CONTAINER_IDLE_TTL` | `1800` | Idle seconds before stopping a container |
| `HOST_DATA_ROOT` | — | Absolute host data path (required when main server runs in Docker) |
| `DATA_ROOT` | `./data` | Runtime data directory |
| `AGENT_TASK_TIMEOUT` | `600` | Max agent task duration (seconds) |
| `MAX_TURNS` | `200` | Max conversation turns per task |
| `MAX_UPLOAD_BYTES` | `200 MB` | Max upload file size |
| `MCP_ENCRYPTION_KEY` | — | Key for encrypting MCP credentials at rest |

> See `.env.example` for all variables: logging, sandbox, prompt limits, resource quotas.

## Architecture

```
Browser (React) ── REST / WebSocket ──► FastAPI (main_server.py)
                                           │
                                           ├── Auth (JWT + bcrypt + CSRF)
                                           ├── Session + Message Store (SQLite)
                                           ├── Skills, MCP, Tasks, Evolution
                                           ├── Container Manager (Docker, optional)
                                           └── Agent SDK (subprocess)
```

## API

### Auth

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/auth/me` | Current user info (role) |
| `GET` | `/api/auth/config` | Auth configuration |
| `POST` | `/api/auth/token` | Login |
| `POST` | `/api/auth/register` | Register |

### Sessions & Files

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/users/{uid}/sessions` | Create session |
| `GET` | `/api/users/{uid}/sessions` | List sessions |
| `DELETE` | `/api/users/{uid}/sessions/{id}` | Delete session |
| `PATCH` | `/api/users/{uid}/sessions/{id}/title` | Rename session |
| `POST` | `/api/users/{uid}/sessions/{id}/cancel` | Cancel running session |
| `POST` | `/api/users/{uid}/sessions/{id}/fork` | Fork session |
| `POST` | `/api/users/{uid}/upload` | Upload file |
| `DELETE` | `/api/users/{uid}/files/{path}` | Delete file |
| `GET` | `/api/users/{uid}/download/{path}` | Download file |

### WebSocket

| Method | Path | Description |
|--------|------|-------------|
| `WS` | `/ws` | Real-time streaming (auth via httpOnly cookie) |

## Development

```bash
# Backend
uv run pytest                          # tests
uv run ruff check src/ main_server.py  # lint
uv run mypy src/                       # type check

# Frontend
cd frontend && npm test                # tests
npx tsc --noEmit                       # type check
```

## Deployment

```bash
docker compose up -d --build           # Docker (recommended)
./scripts/build.sh && ./scripts/manage.sh start   # Manual
```

In production mode (`PROD=true`), the backend serves built frontend assets at port 8000.

## Container Isolation

When `CONTAINER_MODE=true`, each user gets a Docker container with isolated SDK, workspace, and skills.

```bash
docker build -t web-agent-user:latest -f Dockerfile.user .
# Set CONTAINER_MODE=true in .env
docker compose up -d --build
```

| Endpoint | Description |
|----------|-------------|
| `POST /api/users/{uid}/containers/start` | Start container |
| `POST /api/users/{uid}/containers/pause` | Pause container |
| `DELETE /api/users/{uid}/containers` | Destroy container |
| `GET /api/admin/containers` | List all containers |
| `GET /api/admin/resources` | Resource usage |

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Port in use | `./scripts/manage.sh stop` or `pkill -f uvicorn` |
| Frontend can't connect | Backend on port 8000? Check Vite proxy config |
| Agent stuck | Auto-recovery triggers after 30s buffer stall |
| SQLite locked | WAL mode handles most cases; remove stale `.lock` |
| Docker build fails in China | Configure registry mirror + PyPI mirror (see `.env.example`) |
| Container image not found | `docker build -t web-agent-user:latest -f Dockerfile.user .` |

## License

MIT

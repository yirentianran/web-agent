# Web Agent

Multi-user web agent platform powered by Claude Agent SDK — isolated sessions, real-time WebSocket streaming, persistent memory, and private workspaces.

**Tech stack**: FastAPI + React + SQLite + Claude Agent SDK

## Features

### Core
- **Multi-user isolation** — independent sessions, files, memory, and workspace per user
- **Real-time streaming** — WebSocket chat with progressive text rendering and tool-call visualization
- **Session management** — create, rename, fork, and delete sessions; auto-generated titles; full message history
- **Sub-agent tasks** — TaskCreate / TaskUpdate / TaskList integration with lifecycle tracking

### Auth & Security
- **Password authentication** — register and login with bcrypt-hashed passwords; JWT tokens (24h expiry)
- **Optional enforcement** — auth is opt-in via `ENFORCE_AUTH=true`; works without auth for local/dev use
- **Per-user data isolation** — all sessions, files, skills, and memory are scoped to the authenticated user

### File & Workspace
- **File upload** — provide files as agent context (PDF, Excel, CSV supported)
- **Generated files** — browse and download agent outputs per session

### Skills
- **Skill sharing** — upload skills (ZIP), browse and install from the skill library
- **Rating & feedback** — collect user ratings and comments per skill
- **Evolution pipeline** — aggregate feedback over time with improvement suggestions

### Memory
- **L1 cross-session memory** — persistent user preferences and entity memory (SQLite)
- **L2 agent memory** — Markdown files auto-loaded into the agent system prompt

### MCP Registry
- **Admin-managed MCP servers** — register and configure MCP tool servers per user

### Container Isolation (optional)
- **Per-user Docker containers** — fully isolated runtime with separate workspace, skills, and Claude data
- **Idle TTL auto-stop** — containers stop after inactivity to save resources
- **Resource monitoring** — CPU, memory, and disk usage tracking per container

### UX
- **Dark / light theme** — system-aware with manual toggle
- **Internationalization** — English and Chinese, switchable in the header
- **Stuck-agent recovery** — auto-detects stalled sessions and recovers state

## Quick Start

### Docker

```bash
cp .env.example .env
# Edit .env with your API key
docker compose up -d --build
```

Open `http://localhost:8000`.

> **Users in China**: Configure Docker registry and PyPI mirrors first — see [Troubleshooting](#troubleshooting).

### Manual

**Requirements**: Python 3.12+, Node.js 18+, npm 9+

```bash
# macOS / Linux
./setup.sh
cp .env.example .env   # edit with your API key
./start-dev.sh

# Windows (PowerShell)
.\setup.ps1
cp .env.example .env   # edit with your API key
.\start-dev.ps1
```

Backend at `http://127.0.0.1:8000`, frontend dev server at `http://127.0.0.1:3000`. Open the frontend URL in your browser.

> **Windows**: Use `127.0.0.1` — `localhost` resolves to IPv6 first and can break WebSocket connections.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_AUTH_TOKEN` | Yes | — | Anthropic API key (`sk-ant-api03-...`) or Bailian key (`sk-sp-...`) |
| `ANTHROPIC_BASE_URL` | No | Anthropic default | Custom API endpoint (e.g., Bailian: `https://coding.dashscope.aliyuncs.com/apps/anthropic`) |
| `MODEL` | No | `claude-sonnet-4-6` | Model name (e.g., `qwen3.6-plus` for Bailian) |
| `DATA_ROOT` | No | `./data` | Runtime data directory |
| `DATA_DB_PATH` | No | `./data/web-agent.db` | SQLite database path |
| `AGENT_TASK_TIMEOUT` | No | `300` | Max agent task duration (seconds) |
| `MAX_TURNS` | No | `200` | Max conversation turns per task |
| `LOG_LEVEL` | No | `info` | Logging level (debug, info, warning, error) |
| `PROD` | No | `false` | Production mode — serves frontend static files from backend |
| `ENFORCE_AUTH` | No | `false` | Require JWT authentication for all endpoints |
| `JWT_SECRET` | No | auto-generated | JWT signing secret (set in production) |
| `CONTAINER_MODE` | No | `false` | Enable per-user Docker container isolation |
| `CONTAINER_IDLE_TTL` | No | `1800` | Idle seconds before stopping a container |
| `MAX_UPLOAD_BYTES` | No | `209715200` | Max upload file size (200 MB) |
| `TOOL_RESULT_MAX_CHARS` | No | `500` | Max chars per tool result in context |
| `RESOURCE_MAX_CPU_PERCENT` | No | `100` | Per-container CPU limit |
| `RESOURCE_MAX_MEMORY_MB` | No | `4096` | Per-container memory limit (MB) |
| `RESOURCE_MAX_DISK_MB` | No | `1024` | Per-container disk limit (MB) |

## Architecture

```
Browser (React) ── REST / WebSocket ──► FastAPI (main_server.py)
                                           │
                                           ├── Auth (JWT + bcrypt)
                                           ├── Session Store (SQLite)
                                           ├── Message Buffer (in-memory + JSONL disk)
                                           ├── Memory Manager (L1 SQLite + L2 Markdown)
                                           ├── Skill Feedback & Evolution
                                           ├── MCP Server Store (SQLite)
                                           ├── Sub-Agent Task Manager
                                           ├── Container Manager (Docker, optional)
                                           └── Claude Agent SDK (subprocess)
                                                 → tools, hooks, streaming
```

With container isolation enabled, the agent SDK runs inside per-user containers:

```
Browser ──► main_server ──► web-agent-alice (Docker)   ← isolated SDK
                           └── web-agent-bob (Docker)   ← isolated SDK
```

## Project Structure

```
web-agent/
├── main_server.py              # FastAPI entry point (REST + WebSocket)
├── agent_server.py             # Agent endpoint (runs inside user containers)
├── src/                        # Backend modules
│   ├── auth.py                 # JWT + bcrypt password auth
│   ├── database.py             # SQLite with aiosqlite, WAL mode
│   ├── session_store.py        # Session CRUD
│   ├── message_buffer.py       # Message persistence and streaming
│   ├── memory.py               # L1 + L2 user memory
│   ├── mcp_store.py            # MCP server registry
│   ├── skill_feedback.py       # Skill rating and aggregation
│   ├── skill_evolution.py      # Feedback-driven improvement pipeline
│   ├── sub_agent.py            # Sub-agent task lifecycle
│   ├── container_manager.py    # Per-user Docker container management
│   ├── sandbox.py              # Code execution isolation adapter
│   ├── resource_manager.py     # Container resource monitoring
│   └── hooks/                  # PreToolUse, PostToolUse, Stop hooks
├── frontend/src/               # React SPA (Vite)
│   ├── components/             # ChatArea, MessageBubble, InputBar, Sidebar, etc.
│   ├── hooks/                  # useWebSocket, useStreamingText, useSkillsApi, etc.
│   └── lib/                    # Types, session-state, todos, uuid
├── tests/                      # Backend pytest tests
├── scripts/                    # build.sh, manage.sh, verify scripts, migrations
├── data/                       # Runtime data (never committed)
├── Dockerfile                  # Main server image
├── Dockerfile.user             # Per-user agent container image
└── docker-compose.yml
```

## Container Isolation

When `CONTAINER_MODE=true`, each user gets an isolated Docker container running their own Claude Agent SDK instance. The main server bridges WebSocket connections to these containers.

### Setup

```bash
# 1. Build the user container image
docker build -t web-agent-user:latest -f Dockerfile.user .

# 2. Enable in .env
CONTAINER_MODE=true
CONTAINER_IDLE_TTL=1800     # idle seconds before auto-stop (default 30 min)

# 3. Start the main server
docker compose up -d --build
```

### How it works

- On first request, `container_manager.py` creates a container for the user (`web-agent-<user-id>`)
- Volumes are mounted per-user: `/workspace`, `/home/agent/.claude` (sessions, memory, skills)
- A background idle monitor stops containers after `CONTAINER_IDLE_TTL` seconds of inactivity
- When the user returns, the container is restarted automatically
- Resource limits are configurable per container (CPU, memory, disk)

### Container API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/admin/containers` | List all active containers |
| `POST` | `/api/users/{uid}/containers/start` | Start or resume container |
| `POST` | `/api/users/{uid}/containers/pause` | Pause container |
| `DELETE` | `/api/users/{uid}/containers` | Destroy container |
| `GET` | `/api/users/{uid}/resources` | View resource usage |
| `GET` | `/api/admin/resources` | View all resource usage (admin) |

## API

### Auth

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/token` | Login — get JWT token |
| `POST` | `/api/auth/register` | Register new user |

### Sessions

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/users/{uid}/sessions` | Create session |
| `GET` | `/api/users/{uid}/sessions` | List sessions |
| `DELETE` | `/api/users/{uid}/sessions/{id}` | Delete session |
| `GET` | `/api/users/{uid}/sessions/{id}/history` | Get message history |
| `GET` | `/api/users/{uid}/sessions/{id}/status` | Get live session state |
| `PATCH` | `/api/users/{uid}/sessions/{id}/title` | Auto-generate title |
| `POST` | `/api/users/{uid}/sessions/{id}/cancel` | Cancel running session |
| `POST` | `/api/users/{uid}/sessions/{id}/fork` | Fork session |

### Files

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/users/{uid}/upload` | Upload file |
| `GET` | `/api/users/{uid}/generated-files` | List generated files |
| `GET` | `/api/users/{uid}/download/{path}` | Download file |

### Skills

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/users/{uid}/skills/upload` | Upload skill (ZIP) |
| `GET` | `/api/users/{uid}/skills` | List available skills |
| `POST` | `/api/skills/{name}/feedback` | Rate a skill |
| `GET` | `/api/skills/{name}/evolution` | Get skill evolution data |

### Memory

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/users/{uid}/memory` | Get user memory |
| `PUT` | `/api/users/{uid}/memory` | Update user memory |

### MCP (admin)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/admin/mcp` | List MCP servers |
| `POST` | `/api/admin/mcp` | Register MCP server |
| `PUT` | `/api/admin/mcp/{name}` | Update MCP server |
| `DELETE` | `/api/admin/mcp/{name}` | Remove MCP server |

### WebSocket

| Method | Path | Description |
|--------|------|-------------|
| `WS` | `/ws?token=<jwt>` | Real-time agent streaming |

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Server health check |

## Development

```bash
# Backend
uv run pytest              # tests
uv run ruff check src/     # lint
uv run mypy src/           # type check

# Frontend
cd frontend
npm test                   # tests
npx tsc --noEmit           # type check
```

## Deployment

```bash
# Docker (recommended)
docker compose up -d --build

# Manual
./scripts/build.sh           # build frontend assets
./scripts/manage.sh start    # start server in background
./scripts/manage.sh status   # check status
./scripts/manage.sh logs     # view logs
./scripts/manage.sh stop     # stop server
```

The Docker image includes a health check at `/api/health`. Data persists in the `web-agent-data` volume. In production mode (`PROD=true`), the backend serves the built frontend assets directly — access at `http://<server-ip>:8000`.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Agent stuck on "working" | Timer recovery triggers automatically after 30s of stale buffer |
| Port already in use | `./scripts/manage.sh stop` or `pkill -f uvicorn` |
| Frontend can't connect | Verify backend is running on port 8000 |
| SQLite locked | WAL mode handles most cases; remove stale `.lock` file only if no process holds the DB |
| Container image not found | Run `docker build -t web-agent-user:latest -f Dockerfile.user .` |
| PowerShell script blocked | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |

### Docker issues in China

1. **Registry mirror** — add to `~/.docker/daemon.json`:
   ```json
   {
     "registry-mirrors": [
       "https://docker.1ms.run",
       "https://docker.xuanyuan.me"
     ]
   }
   ```
   Restart Docker Desktop after saving.

2. **PyPI mirror** — the Dockerfile uses Tsinghua mirror by default. To change, edit the index URLs in `Dockerfile`.

3. **Clash/VPN interference** — TUN-mode proxies can block Docker outbound traffic. Temporarily disable them before building.

## License

MIT

# Web Agent

Multi-user web agent powered by Claude Agent SDK. Each user gets isolated sessions, a private workspace, persistent memory, and real-time WebSocket streaming.

## Features

- **Multi-user isolation** — independent sessions, files, memory, and workspace per user
- **Real-time streaming** — WebSocket-based chat with progressive text display and tool call visualization
- **Session management** — create, switch, delete, fork sessions with full history and auto-generated titles
- **File support** — upload files for agent context, browse and download agent-generated outputs
- **Skill system** — create, share, and rate reusable skills across users
- **User memory** — persistent preferences and entity memory per user
- **MCP server registry** — admin-managed MCP tool servers
- **Timer persistence** — session timers survive page refresh via localStorage

## Quick Start

### Docker (recommended)

```bash
cp .env.example .env
# Edit .env with your API key, then:
docker compose up -d --build
```

Open `http://localhost:8000`.

> **Users in China**: Configure Docker registry and PyPI mirrors first — see [Troubleshooting](#docker-network-issues-in-china).

### Manual Setup

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

This starts the backend on `http://127.0.0.1:8000` and the frontend dev server on `http://127.0.0.1:3000`. Open `http://127.0.0.1:3000` in your browser.

> **Windows**: Use `127.0.0.1` instead of `localhost` — Windows resolves `localhost` to IPv6 first, which can break WebSocket connections.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key (`sk-ant-api03-...`) or Bailian key (`sk-sp-...`) |
| `ANTHROPIC_BASE_URL` | No | Anthropic default | Custom API endpoint (e.g., Bailian: `https://coding.dashscope.aliyuncs.com/apps/anthropic`) |
| `MODEL` | No | `claude-sonnet-4-6` | Model name |
| `DATA_ROOT` | No | `./data` | Runtime data directory |
| `AGENT_TASK_TIMEOUT` | No | `18000` | Max agent task duration (seconds) |
| `MAX_TURNS` | No | `500` | Max agent turns per session |
| `LOG_LEVEL` | No | `info` | Logging level |

## Architecture

```
┌──────────┐      REST/WS       ┌──────────────────────────────────────┐
│  Browser │ ─────────────────► │  FastAPI (main_server.py)            │
│  (React) │ ◄───────────────── │                                      │
└──────────┘                    │  ┌────────────┐  ┌────────────────┐  │
                                │  │ Session    │  │ MessageBuffer  │  │
                                │  │ Manager    │  │ (in-memory +   │  │
                                │  │            │  │  JSONL disk)   │  │
                                │  └─────┬──────┘  └───────┬────────┘  │
                                │        │                 │           │
                                │  ┌─────▼──────┐  ┌───────▼────────┐  │
                                │  │ Session    │  │ SQLite DB      │  │
                                │  │ Store      │  │ (sessions,     │  │
                                │  │ (JSON)     │  │  messages)     │  │
                                │  └────────────┘  └────────────────┘  │
                                │                                      │
                                │  ┌────────────────────────────────┐  │
                                │  │ Claude Agent SDK (subprocess)  │  │
                                │  │ → tools, hooks, streaming      │  │
                                │  └────────────────────────────────┘  │
                                └──────────────────────────────────────┘
```

## Project Structure

```
web-agent/
├── main_server.py          # FastAPI entry point (REST + WebSocket)
├── agent_server.py         # Agent subprocess endpoint
├── src/                    # Backend modules
│   ├── auth.py             # JWT authentication
│   ├── message_buffer.py   # Session message persistence
│   ├── memory.py           # User memory management
│   ├── hooks/              # Tool call hooks (Write, Bash, etc.)
│   └── ...
├── frontend/               # React SPA
│   └── src/
│       ├── components/     # ChatArea, MessageBubble, StatusSpinner, etc.
│       ├── hooks/          # useWebSocket, useStreamingText
│       └── lib/            # Utilities and type definitions
├── tests/                  # pytest (backend) + Vitest (frontend)
├── skills/                 # Custom skill definitions
├── scripts/                # manage.sh, build.sh
├── data/                   # Runtime data (never committed)
├── Dockerfile
└── docker-compose.yml
```

## API

### Auth & Sessions

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/token` | Get JWT token |
| `POST` | `/api/users/{uid}/sessions` | Create session |
| `GET` | `/api/users/{uid}/sessions` | List sessions |
| `DELETE` | `/api/users/{uid}/sessions/{id}` | Delete session |
| `GET` | `/api/users/{uid}/sessions/{id}/history` | Get message history |
| `GET` | `/api/users/{uid}/sessions/{id}/status` | Get live session state |
| `PATCH` | `/api/users/{uid}/sessions/{id}/title` | Auto-generate title |
| `POST` | `/api/users/{uid}/sessions/{id}/cancel` | Cancel running session |
| `POST` | `/api/users/{uid}/sessions/{id}/fork` | Fork session |

### Files, Skills & Memory

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/users/{uid}/upload` | Upload file |
| `GET` | `/api/users/{uid}/generated-files` | List generated files |
| `GET` | `/api/users/{uid}/download/{path}` | Download file |
| `POST` | `/api/users/{uid}/skills/upload` | Upload skill (ZIP) |
| `GET` | `/api/users/{uid}/skills` | List skills |
| `POST` | `/api/skills/{name}/feedback` | Rate a skill |
| `PUT` | `/api/users/{uid}/memory` | Update user memory |
| `GET` | `/api/users/{uid}/memory` | Get user memory |

### WebSocket

| Method | Path | Description |
|--------|------|-------------|
| `WS` | `/ws?token=<jwt>` | Real-time agent streaming |

## Development

```bash
# Backend tests
uv run pytest

# Frontend tests
cd frontend && npm test

# Format + lint (backend)
uv run ruff format && uv run ruff check src/ main_server.py

# Format + lint + type check (frontend)
cd frontend
npx prettier --write "src/**/*.{ts,tsx}"
npx eslint --fix "src/**/*.{ts,tsx}"
npx tsc --noEmit
```

## Deployment

### Docker Compose

```bash
docker compose up -d --build
```

The image includes a Python-based health check at `/api/health`. Data persists in the `web-agent-data` Docker volume.

### Manual

```bash
./scripts/build.sh           # build frontend assets
./scripts/manage.sh start    # start server in background
./scripts/manage.sh status   # check status
./scripts/manage.sh logs     # view logs
./scripts/manage.sh stop     # stop server
```

Access at `http://<server-ip>:8000`.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Agent stuck on "working" | Timer recovery triggers automatically after 30s of stale buffer |
| Port already in use | `./scripts/manage.sh stop` or `pkill -f uvicorn` |
| Frontend can't connect | Verify backend is running on port 8000 |
| SQLite locked | Remove stale `.lock` file if no other process holds the DB |
| PowerShell script blocked | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |

### Docker Network Issues in China

If Docker builds fail with timeouts:

1. **Docker registry mirror** — add to `~/.docker/daemon.json`:
   ```json
   {
     "registry-mirrors": [
       "https://docker.1ms.run",
       "https://docker.xuanyuan.me"
     ]
   }
   ```
   Restart Docker Desktop after saving.

2. **PyPI mirror** — the Dockerfile already uses Tsinghua mirror for pip and uv. If you need to change it, edit `Dockerfile` and update the index URLs.

3. **Clash/VPN interference** — system proxy tools (Clash Mi, Clash Verge) in TUN mode can block Docker outbound traffic. Temporarily disable them before building.

## License

MIT

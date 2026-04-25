# Web Agent

Multi-user web agent powered by Claude Agent SDK. Each user gets an isolated workspace with full session history, file management, skill sharing, and real-time WebSocket communication.

## Features

- **Multi-user isolation** — each user has independent sessions, files, and workspace
- **Real-time chat** — WebSocket-based streaming with progress indicators and tool call visualization
- **Session management** — create, switch, delete, and fork sessions with full history
- **File upload & download** — upload files for agent context, download agent-generated outputs
- **Skill system** — create, share, and apply custom skills across users with feedback/ratings
- **User memory** — persistent preferences and entity memory per user
- **MCP server registry** — admin-managed MCP tool servers
- **Streaming output** — progressive text display as the agent generates content
- **Timer persistence** — session timers survive page refresh via localStorage

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI, Uvicorn, aiosqlite |
| AI Agent | Claude Agent SDK |
| Frontend | React 18, TypeScript, Vite, Vitest + Testing Library |
| Communication | WebSocket (real-time) + REST API |
| Testing | pytest (backend), Vitest (frontend) |

## System Requirements

- Python 3.12+
- Node.js 18+
- npm 9+

## Quick Start

### 1. Clone and setup

**Linux/macOS:**
```bash
git clone <repo-url>
cd web-agent
./setup.sh
```

**Windows (PowerShell):**
```powershell
git clone <repo-url>
cd web-agent
.\setup.ps1
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```env
# Anthropic API key
# For direct Anthropic API: sk-ant-api03-...
# For Alibaba Cloud Bailian: sk-sp-...
ANTHROPIC_API_KEY=sk-...

# Anthropic base URL (optional)
# For Alibaba Cloud Bailian: https://coding.dashscope.aliyuncs.com/apps/anthropic
# ANTHROPIC_BASE_URL=https://coding.dashscope.aliyuncs.com/apps/anthropic

# Model name (default: claude-sonnet-4-6)
# MODEL=qwen3.6-plus
```

### 3. Start the dev server

**Linux/macOS:**
```bash
./start-dev.sh
```

**Windows (PowerShell):**
```powershell
.\start-dev.ps1
```

This starts:
- Backend on `http://127.0.0.1:8000` (with auto-reload)
- Frontend on `http://127.0.0.1:3000` (Vite dev server with proxy)

Open `http://127.0.0.1:3000` in your browser and log in with a user ID.

> **Windows note**: Use `127.0.0.1` instead of `localhost`. Windows resolves `localhost` to IPv6 (::1) first,
> which can cause WebSocket connection failures when the backend only listens on IPv4.

## Architecture

```
┌─────────────┐     REST/WS      ┌──────────────────────────────────────┐
│   Browser   │ ────────────────► │  FastAPI (main_server.py)            │
│  (React)    │ ◄──────────────── │                                      │
└─────────────┘                   │  ┌────────────┐  ┌────────────────┐ │
                                  │  │ Session    │  │ MessageBuffer  │ │
                                  │  │ Manager    │  │ (in-memory +   │ │
                                  │  │            │  │  JSONL disk)   │ │
                                  │  └─────┬──────┘  └───────┬────────┘ │
                                  │        │                 │          │
                                  │  ┌─────▼──────┐  ┌───────▼────────┐ │
                                  │  │ Session    │  │ SQLite DB      │ │
                                  │  │ Store      │  │ (sessions,     │ │
                                  │  │ (JSON)     │  │  messages)     │ │
                                  │  └────────────┘  └────────────────┘ │
                                  │                                      │
                                  │  ┌────────────────────────────────┐ │
                                  │  │ Claude Agent SDK (subprocess)  │ │
                                  │  │ → tools, hooks, streaming      │ │
                                  │  └────────────────────────────────┘ │
                                  └──────────────────────────────────────┘
```

### Key Components

| Component | Purpose |
|-----------|---------|
| `main_server.py` | FastAPI entry point — REST endpoints, WebSocket bridge, session lifecycle |
| `MessageBuffer` | In-memory message queue with JSONL disk persistence; manages session state, heartbeats, and history |
| `SessionStore` | JSON-based session metadata persistence on disk |
| `SQLite DB` | Persistent storage for messages and session data |
| `useWebSocket` | React hook handling connection, reconnection, message queue, and send tracking |
| `useStreamingText` | Aggregates `content_block_delta` stream events into progressive text display |

## Project Structure

```
web-agent/
├── main_server.py              # FastAPI server (REST + WebSocket)
├── agent_server.py             # Agent subprocess endpoint
├── src/                        # Backend modules
│   ├── auth.py                 # Authentication & JWT
│   ├── message_buffer.py       # Session message persistence & state
│   ├── memory.py               # User memory management
│   ├── file_validation.py      # Upload file validation
│   ├── truncation.py           # Tool output truncation
│   ├── models.py               # Pydantic models
│   ├── hooks/                  # Tool call hooks (Write, Bash, etc.)
│   └── ...
├── frontend/                   # React frontend
│   ├── src/
│   │   ├── components/         # React components
│   │   │   ├── ChatArea.tsx    # Main chat display with streaming text
│   │   │   ├── MessageBubble.tsx # Message rendering (Markdown, tools, etc.)
│   │   │   └── StatusSpinner.tsx # Agent working indicator with timer
│   │   ├── hooks/
│   │   │   ├── useWebSocket.ts # WebSocket connection & message handling
│   │   │   └── useStreamingText.ts # Text delta aggregation
│   │   ├── lib/                # Utilities & type definitions
│   │   └── styles/             # Global CSS
│   └── package.json
├── tests/                      # Backend & frontend tests
│   ├── unit/                   # pytest backend tests
│   └── ...                     # Vitest frontend tests
├── skills/                     # Custom skill definitions
├── docs/                       # Architecture & planning docs
├── plans/                      # Fix & feature plans
├── scripts/
│   ├── manage.sh               # Production server control
│   ├── build.sh                # Frontend build script
│   └── cleanup_stale_files.py  # Cleanup utility
├── data/                       # Runtime data (never committed)
├── .env.example                # Environment configuration template
├── setup.sh                    # One-time setup script (Linux/macOS)
├── setup.ps1                   # One-time setup script (Windows)
├── start-dev.sh                # Development server launcher (Linux/macOS)
└── start-dev.ps1               # Development server launcher (Windows)
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes | — | API key (Anthropic or Bailian) |
| `ANTHROPIC_BASE_URL` | No | Anthropic default | Custom API endpoint |
| `MODEL` | No | `claude-sonnet-4-6` | Model to use |
| `DATA_ROOT` | No | `./data` | Runtime data directory |
| `AGENT_TASK_TIMEOUT` | No | `18000` | Max agent task duration (seconds) |
| `MAX_TURNS` | No | `500` | Max agent turns per session |
| `LOG_LEVEL` | No | `info` | Logging level |

## API

### Authentication

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/token` | Get JWT token for a user |

### Sessions

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/users/{user_id}/sessions` | Create a session |
| `GET` | `/api/users/{user_id}/sessions` | List sessions |
| `DELETE` | `/api/users/{user_id}/sessions/{id}` | Delete a session |
| `GET` | `/api/users/{user_id}/sessions/{id}/history` | Get session history |
| `GET` | `/api/users/{user_id}/sessions/{id}/status` | Get live session state |
| `PATCH` | `/api/users/{user_id}/sessions/{id}/title` | Auto-generate session title |
| `POST` | `/api/users/{user_id}/sessions/{id}/cancel` | Cancel running session |
| `POST` | `/api/users/{user_id}/sessions/{id}/fork` | Fork a session |

### Files

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/users/{user_id}/upload` | Upload a file |
| `GET` | `/api/users/{user_id}/generated-files` | List generated output files |
| `GET` | `/api/users/{user_id}/download/{path}` | Download a file |

### Skills

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/users/{user_id}/skills/upload` | Upload a skill (ZIP) |
| `GET` | `/api/users/{user_id}/skills` | List skills |
| `POST` | `/api/skills/{name}/feedback` | Submit skill feedback |

### Memory

| Method | Path | Description |
|--------|------|-------------|
| `PUT` | `/api/users/{user_id}/memory` | Update user memory |
| `GET` | `/api/users/{user_id}/memory` | Get user memory |

### WebSocket

| Method | Path | Description |
|--------|------|-------------|
| `WS` | `/ws` | Real-time agent communication (supports `?token=` auth) |

## Development

### Running Tests

```bash
# Backend
uv run pytest

# Frontend
cd frontend && npm test

# All frontend tests with coverage
cd frontend && npm test -- --coverage
```

### Code Formatting & Linting

```bash
# Backend (ruff)
uv run ruff format
uv run ruff check src/ main_server.py

# Frontend (prettier + eslint + tsc)
cd frontend
npx prettier --write "src/**/*.{ts,tsx}"
npx eslint --fix "src/**/*.{ts,tsx}"
npx tsc --noEmit
```

### Development Workflow

1. **Research & Reuse** — check GitHub, package registries, and existing skills first
2. **Plan** — create a plan in `plans/` before implementing
3. **TDD** — write tests first, then implement
4. **Code Review** — review after writing code
5. **Commit** — follow conventional commits format

### Rules System

This project uses a layered configuration system in `~/.claude/rules/`:

```
rules/
├── common/          # Language-agnostic principles
├── web/             # Web/frontend specific
├── typescript/      # TypeScript/JavaScript specific
├── python/          # Python specific
└── zh/              # Chinese translations
```

Rules define coding standards, testing requirements, and development workflows. See `rules/README.md` for details.

## Deployment

### Production Setup

1. **Build frontend assets**:
   ```bash
   ./scripts/build.sh
   ```

2. **Start the server**:
   ```bash
   ./scripts/manage.sh start
   ```

3. **Server management**:
   | Command | Description |
   |---------|-------------|
   | `./scripts/manage.sh start` | Start server (background) |
   | `./scripts/manage.sh stop` | Stop server |
   | `./scripts/manage.sh restart` | Restart server |
   | `./scripts/manage.sh status` | Check server status |
   | `./scripts/manage.sh logs` | View server logs |

4. **Access**: Open `http://<server-ip>:8000`

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Agent stuck on "working" after refresh | Timer recovery kicks in automatically after stale buffer detection (30s) |
| Port already in use | Linux/macOS: `./scripts/manage.sh stop` or `pkill -f uvicorn`<br>Windows: `Get-Process uvicorn \| Stop-Process -Force` |
| Frontend fails to connect | Verify backend is running on port 8000; check `.env` config |
| Skill not loading | Ensure skill is in `skills/` directory and properly installed |
| SQLite locked | Check no other process is holding the DB; remove `.lock` if stale |
| PowerShell script blocked | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` first |

## License

MIT

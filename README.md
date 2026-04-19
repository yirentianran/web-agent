# Web Agent

Multi-user web agent powered by Claude Agent SDK. Each user gets an isolated workspace with full session history, file management, skill sharing, and real-time WebSocket communication.

## Features

- **Multi-user isolation** — each user has independent sessions, files, and workspace
- **Real-time chat** — WebSocket-based streaming with progress indicators
- **Session management** — create, switch, delete, and fork sessions with full history
- **File upload & download** — upload files for agent context, download agent-generated outputs
- **Skill system** — create, share, and apply custom skills across users
- **User memory** — persistent preferences and entity memory per user
- **MCP server registry** — admin-managed MCP tool servers
- **Feedback & ratings** — user feedback collection per session

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI, Uvicorn |
| AI Agent | Claude Agent SDK |
| Frontend | React 18, TypeScript, Vite |
| Communication | WebSocket (real-time) + REST API |
| Testing | pytest (backend), Vitest + Testing Library (frontend) |

## Quick Start

### 1. Clone and setup

```bash
git clone <repo-url>
cd web-agent
./setup.sh
```

### 2. Configure your API key

Edit `.env` and set your API key:

```env
# For direct Anthropic API:
ANTHROPIC_API_KEY=sk-ant-api03-...

# For Alibaba Cloud Bailian (Anthropic-compatible):
ANTHROPIC_API_KEY=sk-sp-...
ANTHROPIC_BASE_URL=https://coding.dashscope.aliyuncs.com/apps/anthropic
MODEL=qwen3.6-plus
```

### 3. Start the dev server

```bash
./start-dev.sh
```

This starts:
- Backend on `http://localhost:8000`
- Frontend on `http://localhost:3000`

## Project Structure

```
web-agent/
├── main_server.py          # FastAPI server (REST + WebSocket)
├── agent_server.py         # Agent subprocess endpoint
├── src/                    # Backend modules
│   ├── auth.py             # Authentication & JWT
│   ├── message_buffer.py   # Session message persistence
│   ├── memory.py           # User memory management
│   ├── file_validation.py  # Upload file validation
│   ├── hooks/              # Tool call hooks (Write, Bash, etc.)
│   └── ...
├── frontend/               # React frontend
│   ├── src/
│   │   ├── components/     # React components
│   │   ├── hooks/          # Custom hooks (useWebSocket)
│   │   ├── lib/            # Utilities
│   │   └── styles/         # CSS
│   └── package.json
├── tests/                  # Backend tests
│   └── unit/
├── docs/                   # Architecture docs
├── scripts/                # Management scripts
│   ├── manage.sh           # Production server control (start/stop/restart)
│   └── build.sh            # Frontend build script
└── setup.sh                # One-time setup script
```

## Deployment

The project uses a **single-process architecture** — FastAPI serves both the API and the frontend static files. No Docker or Nginx required.

### Production Setup

1. **Build frontend assets**:
   ```bash
   ./scripts/build.sh
   ```

2. **Start the server**:
   ```bash
   ./scripts/manage.sh start
   ```

3. **Manage the server**:
   | Command | Description |
   |---------|-------------|
   | `./scripts/manage.sh start` | Start server (background) |
   | `./scripts/manage.sh stop` | Stop server (finds process by name) |
   | `./scripts/manage.sh restart` | Restart server |
   | `./scripts/manage.sh status` | Check server status |
   | `./scripts/manage.sh logs` | View server logs |

4. **Access**: Open `http://<server-ip>:8000`

## Development

For local development with hot-reload:

```bash
./start-dev.sh
```

This starts:
- Backend on `http://localhost:8000` (with auto-reload)
- Frontend on `http://localhost:3000` (Vite dev server with proxy)

### Running Tests

**Backend**:
```bash
uv run pytest
```

**Frontend**:
```bash
cd frontend && npm test
```

## API

Key endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/users/{user_id}/sessions` | Create a session |
| `GET` | `/api/users/{user_id}/sessions` | List sessions |
| `DELETE` | `/api/users/{user_id}/sessions/{id}` | Delete a session |
| `GET` | `/api/users/{user_id}/sessions/{id}/history` | Get session history |
| `POST` | `/api/users/{user_id}/upload` | Upload a file |
| `GET` | `/api/users/{user_id}/generated-files` | List generated output files |
| `GET` | `/api/users/{user_id}/download/{path}` | Download a file |
| `POST` | `/api/users/{user_id}/skills/upload` | Upload a skill (ZIP) |
| `GET` | `/api/users/{user_id}/skills` | List skills |
| `PUT` | `/api/users/{user_id}/memory` | Update user memory |
| `GET` | `/api/users/{user_id}/memory` | Get user memory |
| `WS` | `/ws` | WebSocket for real-time agent communication |

## License

MIT

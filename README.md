# Web Agent

Multi-user web agent platform powered by Claude Agent SDK. Each user gets an isolated workspace with full session history, file management, skill sharing, and real-time WebSocket communication.

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
├── docker-compose.yml      # Docker development config
└── setup.sh                # One-time setup script
```

## Development

### Backend

```bash
# Install dependencies
uv sync  # or pip install -e .

# Run tests
uv run pytest

# Run linter
uv run ruff check src/ main_server.py

# Run type check
uv run mypy src/
```

### Frontend

```bash
cd frontend

# Install dependencies
npm install

# Run tests
npm test

# Run type check
npx tsc --noEmit

# Start dev server
npm run dev
```

## API

Interactive API docs at `http://localhost:8000/docs`

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
| `POST` | `/api/users/{user_id}/skills` | Create a skill |
| `GET` | `/api/users/{user_id}/skills` | List skills |
| `PUT` | `/api/users/{user_id}/memory` | Update user memory |
| `GET` | `/api/users/{user_id}/memory` | Get user memory |
| `WS` | `/ws` | WebSocket for real-time agent communication |

## License

MIT

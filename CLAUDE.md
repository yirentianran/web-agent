# CLAUDE.md — Web Agent

## Project Overview

Multi-user web agent built with FastAPI (backend) and React (frontend). Each user gets isolated sessions, workspace, and memory. The backend communicates with Claude Agent SDK via WebSocket streaming.

## Architecture

- **Backend** (`main_server.py`, `agent_server.py`, `src/`) — FastAPI REST + WebSocket server
- **Frontend** (`frontend/src/`) — React SPA with Vite, communicating via REST + WebSocket
- **Data** (`data/`) — Per-user session files, message buffers, uploads, outputs (never committed)

## Key Files

| File | Purpose |
|------|---------|
| `main_server.py` | Main FastAPI app: REST endpoints, WebSocket bridge, session management |
| `agent_server.py` | Agent subprocess FastAPI endpoint |
| `src/message_buffer.py` | Session message persistence and retrieval |
| `src/memory.py` | User memory (preferences, entities, audit context) |
| `src/auth.py` | JWT authentication |
| `frontend/src/App.tsx` | Main React app with session state management |
| `frontend/src/hooks/useWebSocket.ts` | WebSocket hook for real-time communication |

## Common Commands

### Backend

```bash
# Install deps
uv sync

# Run tests
uv run pytest

# Lint + format
uv run ruff format && uv run ruff check src/ main_server.py

# Type check
uv run mypy src/

# Start dev server
uv run uvicorn main_server:app --reload --port 8000
```

### Frontend

```bash
cd frontend

# Install deps
npm install

# Run tests
npm test

# Run dev server
npm run dev

# Type check
npx tsc --noEmit
```

## Testing

- Backend tests: `tests/unit/test_main_server.py` (uses FastAPI TestClient with mocked SDK)
- Frontend tests: `frontend/src/components/*.test.tsx` (Vitest + JSDOM + Testing Library)
- TDD workflow: Write tests first, implement, verify coverage 80%+

## Data Directory

The `data/` directory contains runtime data (sessions, uploads, outputs) and is **never** committed. It's excluded in `.gitignore`.

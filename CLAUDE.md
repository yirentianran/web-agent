# CLAUDE.md — Web Agent

## Project Overview

Multi-user web agent built with FastAPI (backend) and React (frontend). Each user gets isolated sessions, workspace, and memory. The backend communicates with an AI agent SDK via WebSocket streaming (Anthropic-compatible API — works with DeepSeek, Bailian, etc.).

## Architecture

- **Backend** (`main_server.py`, `agent_server.py`, `src/`) — FastAPI REST + WebSocket server
- **Frontend** (`frontend/src/`) — React SPA with Vite, communicating via REST + WebSocket
- **Data** (`data/`) — Per-user session files, message buffers, uploads, outputs (never committed)

## Key Files

| File | Purpose |
|------|---------|
| `main_server.py` | Main FastAPI app: REST endpoints, WebSocket bridge, session management |
| `agent_server.py` | Agent subprocess endpoint (container mode) |
| `src/message_buffer.py` | In-memory message buffer with JSONL disk persistence |
| `src/session_store.py` | DB-backed session CRUD and message storage |
| `src/database.py` | SQLite via aiosqlite, WAL mode |
| `src/auth.py` | JWT + bcrypt password authentication |
| `src/cost.py` | Model name resolution (`MODEL` / `FLASH_MODEL` env vars) |
| `src/observation.py` | ToolObserver — tool-call event recording |
| `src/instinct_extractor.py` | Automatic pattern discovery from observations |
| `src/container_bridge.py` | WebSocket bridge to per-user Docker containers |
| `src/agent_logger.py` | L3 agent execution logging |
| `src/semantic_search.py` | FTS5 search over sessions and wiki pages |
| `frontend/src/App.tsx` | Main React app with session state management |
| `frontend/src/hooks/useWebSocket.ts` | WebSocket hook for real-time communication |

## Environment

- `MODEL` (required) — main agent model, no default
- `FLASH_MODEL` (optional) — lightweight tasks, falls back to `MODEL`
- Copy `.env.example` to `.env` and configure before running

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

- Backend tests: `tests/unit/` — pytest with mocked SDK
- Frontend tests: `frontend/src/**/*.test.tsx` — Vitest + JSDOM + Testing Library
- TDD workflow: Write tests first, implement, verify coverage 80%+

## Data Directory

The `data/` directory contains runtime data (sessions, uploads, outputs) and is **never** committed. It's excluded in `.gitignore`.

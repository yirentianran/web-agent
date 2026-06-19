# CLAUDE.md — Web Agent

## Project Overview

Multi-user web agent built with FastAPI (backend) and React (frontend). Each user gets isolated sessions, workspace, and memory. The backend communicates with an AI agent SDK via WebSocket streaming (Anthropic-compatible API — works with DeepSeek, Bailian, etc.).

## Architecture

- **Backend** (`main_server.py`, `agent_server.py`, `src/`) — FastAPI REST + WebSocket server
- **Agent layer** (`src/agent/`) — Typed InternalEvent protocol, SDK/container adapters, per-mode executors
- **Security layer** (`src/security/`) — Shared enforcer, command/file filters, rate limiter
- **Frontend** (`frontend/src/`) — React SPA with Vite, communicating via REST + WebSocket
- **Data** (`data/`) — Per-user session files, message buffers, uploads, outputs (never committed)

### Frontend Architecture

```
frontend/src/
├── components/        # Feature components
│   ├── MessageBubble.tsx    # Tool cards, Markdown/HTML rendering, tool-result pairing
│   ├── ChatArea.tsx         # Message list, streaming, session switching
│   ├── MarkdownRenderer.tsx # react-markdown with code fences, syntax highlighting
│   └── ...
├── hooks/             # Shared hooks (useWebSocket, useCopyToClipboard, …)
├── lib/               # types, api client (apiFetch), todos parser
├── i18n/              # en.json, zh.json — always update both
└── styles/            # global.css with CSS custom properties
```

Key rendering pipeline: `MessageBubble` → `ToolResultContent` (content-type detection: HTML → direct render, Markdown → direct render, JSON/Bash/text → code fence) → `MarkdownRenderer` (react-markdown + highlight.js)

## Key Files

| File | Purpose |
|------|---------|
| `main_server.py` | Main FastAPI app: REST endpoints, WebSocket bridge, session management |
| `agent_server.py` | Agent subprocess endpoint (container mode) |
| `src/message_buffer.py` | In-memory message buffer with JSONL disk persistence |
| `src/session_store.py` | DB-backed session CRUD and message storage |
| `src/database.py` | SQLite via aiosqlite, WAL mode |
| `src/auth.py` | JWT + bcrypt auth, CSRF protection, httpOnly cookies |
| `src/admin_auth.py` | Admin role verification (cookie + header fallback) |
| `src/cost.py` | Model name resolution (`MODEL` / `FLASH_MODEL` env vars) |
| `src/observation.py` | ToolObserver — tool-call event recording |
| `src/instinct_extractor.py` | Automatic pattern discovery from observations |
| `src/agent/protocol.py` | Typed InternalEvent protocol — frozen dataclass union for all event types |
| `src/agent/adapters/sdk.py` | Adapter: Claude Agent SDK dataclass → InternalEvent |
| `src/agent/adapters/container_json.py` | Adapter: container WebSocket JSON dict → InternalEvent |
| `src/agent/local.py` | LocalAgentExecutor — runs agent via ClaudeSDKClient in-process |
| `src/agent/container.py` | ContainerAgentExecutor — runs agent in per-user Docker container |
| `src/agent/options.py` | Unified AgentOptions builder for both local and container modes |
| `src/agent/prompt.py` | Prompt builders: history compilation, language directives, attachments |
| `src/event_pipeline.py` | Shared event pipeline: process_event, _finish_task, handle_task_error |
| `src/security/enforcer.py` | SecurityEnforcer — shared pre-execution security checks |
| `src/security/filters.py` | OutputFilter, BashCommandFilter, FileAccessFilter |
| `src/security/rate_limiter.py` | Per-session sliding-window tool call rate limiter |
| `src/container_bridge.py` | WebSocket bridge to per-user Docker containers |
| `src/container_manager.py` | Per-user Docker container lifecycle |
| `src/mcp_store.py` | MCP server registry with credential encryption |
| `src/skill_manager.py` | Skill upload, download, promote, filesystem migration |
| `src/agent_logger.py` | L3 agent execution logging |
| `src/semantic_search.py` | FTS5 search over sessions and wiki pages |
| `frontend/src/App.tsx` | Main React app: session state, routing, auth |
| `frontend/src/components/MessageBubble.tsx` | Tool cards, content rendering, tool-result pairing |
| `frontend/src/components/MarkdownRenderer.tsx` | Markdown rendering with code fences and syntax highlighting |
| `frontend/src/hooks/useWebSocket.ts` | WebSocket hook for real-time communication |
| `frontend/src/lib/api.ts` | `apiFetch` wrapper (auto CSRF header, credentials) |
| `frontend/src/components/SettingsMenu.tsx` | Admin menu: dashboard, users, MCP, evolution |

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
uv run ruff format && uv run ruff check src/ main_server.py agent_server.py

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

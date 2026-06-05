# Contributing

## Setup

```bash
./setup.sh                    # install dependencies
cp .env.example .env          # set ANTHROPIC_AUTH_TOKEN and MODEL
./start-dev.sh                # start dev servers
```

See [CLAUDE.md](CLAUDE.md) for commands (lint, type check, test).

## Standards

### Python

- PEP 8 + type annotations on all function signatures
- Format: `ruff format`, lint: `ruff check`, types: `mypy`
- Tests: `pytest` (80%+ coverage)

### TypeScript

- Explicit types on exported functions and component props
- Format: Prettier, lint: ESLint, types: `tsc --noEmit`
- Tests: Vitest + Testing Library

## PR Process

1. Branch from `main`
2. Write / update tests
3. `uv run pytest && cd frontend && npm test`
4. `uv run mypy src/ && npx tsc --noEmit`
5. Submit PR with a clear description

## Commits

```
feat: add skill sharing between users
fix: handle empty session history gracefully
docs: update API documentation
test: add WebSocket reconnection tests
refactor: extract message buffer into separate module
```

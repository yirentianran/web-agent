# Contributing

## Setup

```bash
./setup.sh                    # install dependencies
cp .env.example .env          # set ANTHROPIC_AUTH_TOKEN and MODEL
./start-dev.sh                # start dev servers
```

See [CLAUDE.md](CLAUDE.md) for dev commands (lint, type check, test).

## Standards

### Python

- PEP 8 + type annotations on all function signatures
- Format: `ruff format`, lint: `ruff check`, types: `mypy`
- Tests: `pytest` (80%+ coverage)

### TypeScript / React

- Explicit types on exported functions and component props
- Format: Prettier, lint: ESLint, types: `tsc --noEmit`
- Tests: Vitest + Testing Library (80%+ coverage)
- Components: compound components for related UI, hooks for shared logic
- Shared utilities: extract to `src/hooks/` or `src/lib/`

### Internationalization

- All user-facing strings go through `useTranslation()` — never hardcode text
- Always add keys to both `en.json` and `zh.json`
- Key names use camelCase, nested by feature (`message.copyResult`, `input.placeholder`)

## PR Process

1. Branch from `main`
2. Write / update tests
3. `uv run pytest && cd frontend && npm test`
4. `uv run mypy src/ && npx tsc --noEmit`
5. Submit PR with a clear description and test plan

## Code Review

All PRs require review. Key things reviewers check:

- Tests cover new behavior and edge cases
- No hardcoded secrets or credentials
- i18n keys added to both `en.json` and `zh.json`
- No dead code left behind (unused imports, deleted but not cleaned up CSS)
- Component naming follows project conventions (PascalCase components, camelCase hooks)

## Commits

```
feat: add skill sharing between users
fix: handle empty session history gracefully
docs: update API documentation
test: add WebSocket reconnection tests
refactor: extract message buffer into separate module
```

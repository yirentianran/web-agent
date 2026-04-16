# Contributing

## Development Setup

1. Run `./setup.sh` to install dependencies
2. Copy `.env.example` to `.env` and configure your API key
3. Start dev servers with `./start-dev.sh`

## Code Standards

### Python

- Follow PEP 8
- Add type annotations to all function signatures
- Format with `ruff format`, lint with `ruff check`
- Type check with `mypy`
- Write tests with `pytest` (80%+ coverage)

### TypeScript

- Add explicit types to exported functions and component props
- Let TypeScript infer local variable types
- Format with Prettier, lint with ESLint
- Type check with `tsc --noEmit`
- Write tests with Vitest + Testing Library

## Pull Request Process

1. Create a branch from `main`
2. Make your changes
3. Run tests (`pytest` + `npm test`)
4. Ensure type checks pass (`mypy` + `tsc --noEmit`)
5. Submit a PR with a clear description

## Commit Messages

Use conventional commits:

```
feat: add skill sharing between users
fix: handle empty session history gracefully
docs: update API endpoint documentation
test: add WebSocket reconnection tests
refactor: extract message buffer into separate module
```

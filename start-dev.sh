#!/usr/bin/env bash
# Start both backend and frontend in development mode.
# Backend: uvicorn on port 8000
# Frontend: Vite dev server on port 3000 (proxies /api and /ws to backend)

set -e

cd "$(dirname "$0")"

# Activate virtual environment
if [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
fi

# Ensure frontend dependencies are installed
if [ ! -d "frontend/node_modules" ]; then
  echo "Installing frontend dependencies..."
  (cd frontend && npm install)
fi

echo "Starting backend (uvicorn :8000) + frontend (vite :3000)..."

# uvicorn without --reload to prevent WebSocket disconnect on skill creation
npx concurrently \
  --names "API,WEB" \
  --prefix-colors "blue,green" \
  "uvicorn main_server:app --host 0.0.0.0 --port 8000" \
  "cd frontend && npm run dev"

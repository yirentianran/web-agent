#!/usr/bin/env bash
# Start both backend and frontend in development mode.
# Backend: uvicorn on port 8000 (with hot reload)
# Frontend: Vite dev server on port 3000 (proxies /api and /ws to backend)

set -e

cd "$(dirname "$0")"

# Ensure unbuffered Python output
export PYTHONUNBUFFERED=1

# Activate virtual environment
if [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
fi

# Load environment variables from .env
if [ -f ".env" ]; then
  set -a
  source .env
  set +a
fi

# Override data paths for local development (relative to project root)
export DATA_ROOT=./data
export DATA_DB_PATH=./data/web-agent.db

# Ensure frontend dependencies are installed
if [ ! -d "frontend/node_modules" ]; then
  echo "Installing frontend dependencies..."
  (cd frontend && npm install)
fi

echo "Starting backend (uvicorn :8000) + frontend (vite :3000)..."

# --- Cleanup existing processes ---
echo "Checking for existing processes..."

# Kill existing backend (uvicorn main_server:app)
PIDS=$(pgrep -f "uvicorn main_server:app" || true)
if [ -n "$PIDS" ]; then
  echo "  Killing backend processes: $PIDS"
  kill $PIDS 2>/dev/null || true
  sleep 1
  # Force kill if still running
  REMAINING=$(pgrep -f "uvicorn main_server:app" || true)
  if [ -n "$REMAINING" ]; then
    echo "  Force killing backend..."
    kill -9 $REMAINING 2>/dev/null || true
  fi
  echo "  Backend stopped."
else
  echo "  No existing backend process found."
fi

# Kill existing frontend (Node process on port 3000)
FPID=$(lsof -ti:3000 2>/dev/null || true)
if [ -n "$FPID" ]; then
  echo "  Killing frontend processes on port 3000: $FPID"
  kill $FPID 2>/dev/null || true
  sleep 1
  # Force kill if still running
  REMAINING=$(lsof -ti:3000 2>/dev/null || true)
  if [ -n "$REMAINING" ]; then
    echo "  Force killing frontend..."
    kill -9 $REMAINING 2>/dev/null || true
  fi
  echo "  Frontend stopped."
else
  echo "  No existing frontend process found."
fi

# Use concurrently to run both processes
# --kill-others-on-fail: if one fails, kill the other
# --handle-input: allow sending input to processes
npx concurrently \
  --kill-others-on-fail \
  --handle-input \
  --names "API,WEB" \
  --prefix-colors "blue,green" \
  "uvicorn main_server:app --host 0.0.0.0 --port 8000 --log-level info" \
  "cd frontend && npm run dev"

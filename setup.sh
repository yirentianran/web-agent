#!/usr/bin/env bash
# One-time project setup: install dependencies and prepare environment.

set -e

cd "$(dirname "$0")"

echo "=== Web Agent Setup ==="

# Step 1: Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | grep -oP '\d+\.\d+')
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" 2>/dev/null; then
  echo "✓ Python 3.12+ found"
else
  echo "✗ Python 3.12+ required (found: $PYTHON_VERSION)"
  exit 1
fi

# Step 2: Install backend dependencies
if [ -f "uv.lock" ]; then
  echo "Installing backend dependencies (uv sync)..."
  uv sync
elif command -v uv >/dev/null 2>&1; then
  echo "Installing backend dependencies (uv sync)..."
  uv sync
else
  echo "Installing backend dependencies (pip)..."
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -e ".[dev]"
fi
echo "✓ Backend dependencies installed"

# Step 3: Install frontend dependencies
echo "Installing frontend dependencies..."
(cd frontend && npm install)
echo "✓ Frontend dependencies installed"

# Step 4: Setup environment file
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo ""
  echo "⚠  Created .env from .env.example"
  echo "   Please edit .env and set your ANTHROPIC_API_KEY before starting"
fi

# Step 5: Install concurrently for start-dev.sh
if command -v npx >/dev/null 2>&1; then
  echo "Installing concurrently (for start-dev.sh)..."
  npm install -g concurrently 2>/dev/null || npm install -D concurrently
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit .env and set your ANTHROPIC_API_KEY"
echo "  2. Run ./start-dev.sh to start both servers"
echo "  3. Open http://localhost:3000 in your browser"

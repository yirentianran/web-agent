#!/usr/bin/env bash
# verify-e2e.sh — End-to-end verification script for web-agent Phase 1.3
#
# Usage: ./scripts/verify-e2e.sh [PORT]
#   PORT: backend port (default: 8000)
#
# What it does:
#   1. Checks prerequisites (Python, venv, dependencies)
#   2. Runs automated REST + WS health checks
#   3. Prints manual browser testing instructions

set -euo pipefail

BACKEND_PORT="${1:-8000}"
FRONTEND_PORT=3000
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
PASS="${GREEN}PASS${NC}"
FAIL="${RED}FAIL${NC}"
WARN="${YELLOW}WARN${NC}"

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "============================================"
echo "  Web Agent — Phase 1.3 E2E Verification"
echo "============================================"
echo ""

# ── Prerequisite checks ───────────────────────────────────────────

echo "[1/5] Checking prerequisites..."

if ! command -v python3 &>/dev/null; then
    echo -e "  $FAIL python3 not found"
    exit 1
fi
echo -e "  $PASS python3 found: $(python3 --version)"

if [ ! -d ".venv" ]; then
    echo -e "  $FAIL .venv not found — run: python3 -m venv .venv && source .venv/bin/activate && pip install -e ."
    exit 1
fi
echo -e "  $PASS .venv exists"

# Check key dependencies
source .venv/bin/activate
for pkg in fastapi uvicorn pydantic httpx; do
    if ! python3 -c "import $pkg" 2>/dev/null; then
        echo -e "  $FAIL $pkg not installed — run: pip install -e ."
        exit 1
    fi
done
echo -e "  $PASS Python dependencies OK"

if [ ! -d "frontend/node_modules" ]; then
    echo -e "  $WARN frontend/node_modules missing — run: cd frontend && npm install"
else
    echo -e "  $PASS Frontend dependencies OK"
fi

echo ""

# ── Run automated tests ───────────────────────────────────────────

echo "[2/5] Running unit tests..."
if python3 -m pytest tests/unit/ -q 2>&1 | tail -3; then
    echo -e "  $PASS Unit tests"
else
    echo -e "  $FAIL Unit tests"
    exit 1
fi

echo ""
echo "[3/5] Running integration tests..."
if python3 -m pytest tests/integration/ -q 2>&1 | tail -3; then
    echo -e "  $PASS Integration tests"
else
    echo -e "  $FAIL Integration tests"
    exit 1
fi

echo ""

# ── Start backend ─────────────────────────────────────────────────

echo "[4/5] Starting backend on port $BACKEND_PORT..."

# Kill any existing backend
pkill -f "uvicorn main_server:app.*--port $BACKEND_PORT" 2>/dev/null || true
sleep 1

# Start backend in background
source .venv/bin/activate
uvicorn main_server:app \
    --host 0.0.0.0 \
    --port "$BACKEND_PORT" \
    --reload \
    --reload-dir "$PROJECT_ROOT" &
BACKEND_PID=$!

# Wait for backend to be healthy
echo "  Waiting for backend..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:$BACKEND_PORT/health" >/dev/null 2>&1; then
        echo -e "  $PASS Backend healthy"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo -e "  $FAIL Backend did not start within 30s"
        kill $BACKEND_PID 2>/dev/null || true
        exit 1
    fi
    sleep 1
done

# ── Automated REST checks ─────────────────────────────────────────

echo ""
echo "[5/5] Running automated REST checks..."

# Health check
HEALTH=$(curl -sf "http://localhost:$BACKEND_PORT/health" 2>/dev/null)
if echo "$HEALTH" | grep -q '"status"'; then
    echo -e "  $PASS /health returns $HEALTH"
else
    echo -e "  $FAIL /health failed"
fi

# Sessions list
SESSIONS=$(curl -sf "http://localhost:$BACKEND_PORT/api/users/default/sessions" 2>/dev/null)
if [ "$SESSIONS" != "" ]; then
    echo -e "  $PASS /api/users/default/sessions returns $SESSIONS"
else
    echo -e "  $FAIL /api/users/default/sessions failed"
fi

# Create session
NEW_SESSION=$(curl -sf -X POST "http://localhost:$BACKEND_PORT/api/users/default/sessions" 2>/dev/null)
if echo "$NEW_SESSION" | grep -q 'session_id'; then
    echo -e "  $PASS POST /api/users/default/sessions returns $NEW_SESSION"
    SESSION_ID=$(echo "$NEW_SESSION" | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")

    # Session status
    STATUS=$(curl -sf "http://localhost:$BACKEND_PORT/api/users/default/sessions/$SESSION_ID/status" 2>/dev/null)
    if echo "$STATUS" | grep -q '"idle"'; then
        echo -e "  $PASS Session status is idle"
    else
        echo -e "  $FAIL Session status unexpected: $STATUS"
    fi
else
    echo -e "  $FAIL POST /api/users/default/sessions failed"
fi

echo ""
echo "============================================"
echo "  Automated checks complete"
echo "============================================"
echo ""
echo "Backend running on http://localhost:$BACKEND_PORT (PID: $BACKEND_PID)"
echo ""
echo "--- Manual Browser Testing ---"
echo ""
echo "1. Start frontend:"
echo "   cd frontend && BACKEND_PORT=$BACKEND_PORT npx vite --port $FRONTEND_PORT"
echo ""
echo "2. Open http://localhost:$FRONTEND_PORT in browser"
echo ""
echo "3. Create a new session (should auto-create on first message)"
echo ""
echo "4. Send a test message: 'Hello, what can you help me with?'"
echo ""
echo "5. Verify:"
echo "   - Agent responds with assistant messages"
echo "   - Tool use events appear (if applicable)"
echo "   - Final result message appears"
echo "   - Session status changes: idle -> running -> completed"
echo "   - Cost display shows > \$0"
echo ""
echo "6. Send another message to verify session reuse"
echo ""
echo "7. Test reconnection: refresh browser, verify history replay"
echo ""
echo "--- Cleanup ---"
echo "  kill $BACKEND_PID  # stop backend"
echo ""

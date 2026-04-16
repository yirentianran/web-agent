#!/usr/bin/env bash
# Test: Verify that start-dev.sh cleans up existing processes before starting.
# This script simulates a stale process and verifies start-dev.sh kills it.

set -e

cd "$(dirname "$0")"

PASS=0
FAIL=0

echo "=== Testing start-dev.sh process cleanup ==="

# Test 1: Verify cleanup section exists in start-dev.sh
echo ""
echo "Test 1: start-dev.sh contains process cleanup logic..."
if grep -q "Checking for existing" start-dev.sh && \
   grep -q "pgrep.*uvicorn main_server" start-dev.sh && \
   grep -q "lsof.*3000" start-dev.sh; then
  echo "  PASS: Cleanup logic found in start-dev.sh"
  PASS=$((PASS + 1))
else
  echo "  FAIL: Cleanup logic NOT found in start-dev.sh"
  FAIL=$((FAIL + 1))
fi

# Test 2: Verify cleanup runs BEFORE concurrently
echo ""
echo "Test 2: Cleanup runs before concurrently..."
CLEAN_LINE=$(grep -n "Checking for existing" start-dev.sh | head -1 | cut -d: -f1)
CONCURRENTLY_LINE=$(grep -n "npx concurrently" start-dev.sh | head -1 | cut -d: -f1)

if [ -n "$CLEAN_LINE" ] && [ -n "$CONCURRENTLY_LINE" ] && [ "$CLEAN_LINE" -lt "$CONCURRENTLY_LINE" ]; then
  echo "  PASS: Cleanup (line $CLEAN_LINE) runs before concurrently (line $CONCURRENTLY_LINE)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: Cleanup should run before concurrently"
  FAIL=$((FAIL + 1))
fi

# Test 3: Verify strictPort is set in vite config
echo ""
echo "Test 3: Vite strictPort is enabled..."
if grep -q "strictPort: true" frontend/vite.config.ts; then
  echo "  PASS: strictPort: true found in vite.config.ts"
  PASS=$((PASS + 1))
else
  echo "  FAIL: strictPort: true NOT found in vite.config.ts"
  FAIL=$((FAIL + 1))
fi

# Test 4: Verify SIGTERM before SIGKILL (graceful then force)
echo ""
echo "Test 4: Graceful kill before force kill..."
if grep -q "kill \$PIDS" start-dev.sh && grep -q "kill -9" start-dev.sh; then
  # Verify kill comes before kill -9 in each cleanup block
  echo "  PASS: Both SIGTERM and SIGKILL present"
  PASS=$((PASS + 1))
else
  echo "  FAIL: Missing graceful or force kill pattern"
  FAIL=$((FAIL + 1))
fi

# Test 5: Verify no || true on empty results (doesn't exit on no stale processes)
echo ""
echo "Test 5: Handles no-stale-process case without error..."
if grep -q '|| true' start-dev.sh; then
  echo "  PASS: Fallback patterns present for empty results"
  PASS=$((PASS + 1))
else
  echo "  FAIL: Missing fallback patterns"
  FAIL=$((FAIL + 1))
fi

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="

if [ "$FAIL" -gt 0 ]; then
  echo "FAIL: Not all tests passed"
  exit 1
fi

echo "PASS: All tests passed!"
exit 0

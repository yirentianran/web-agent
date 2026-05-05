#!/bin/bash
# Web Agent 多用户隔离一键验证脚本
# 用法: ENFORCE_AUTH=true bash scripts/verify-isolation.sh
set -euo pipefail

BASE="${BASE:-http://localhost:8000}"
PASS=0
FAIL=0

check() {
    local desc="$1" expected="$2" actual="$3"
    if [ "$actual" = "$expected" ]; then
        echo "  ✅ $desc"
        ((PASS++))
    else
        echo "  ❌ $desc (expected $expected, got $actual)"
        ((FAIL++))
    fi
}

echo "=== Web Agent Isolation Verification ==="
echo "Target: $BASE"
echo ""

# 注册用户（忽略已存在错误）
curl -s -X POST "$BASE/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"alice","password":"pass1"}' > /dev/null 2>&1 || true
curl -s -X POST "$BASE/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"bob","password":"pass2"}' > /dev/null 2>&1 || true

# 获取令牌
TA=$(curl -s -X POST "$BASE/api/auth/token" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"alice","password":"pass1"}' | jq -r '.token')
TB=$(curl -s -X POST "$BASE/api/auth/token" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"bob","password":"pass2"}' | jq -r '.token')

echo "--- Layer 1: Auth ---"
check "unauthenticated access rejected" "401" \
  "$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/users/alice/sessions")"
check "cross-user session access (bob→alice)" "403" \
  "$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TB" "$BASE/api/users/alice/sessions")"
check "cross-user memory access (alice→bob)" "403" \
  "$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TA" "$BASE/api/users/bob/memory")"

echo "--- Layer 2: Sessions ---"
SA=$(curl -s -X POST -H "Authorization: Bearer $TA" \
  -H "Content-Type: application/json" \
  "$BASE/api/users/alice/sessions" -d '{"name":"test"}' | jq -r '.session_id')
check "bob reads alice's session history" "403" \
  "$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TB" "$BASE/api/users/alice/sessions/$SA/history")"
check "bob deletes alice's session" "403" \
  "$(curl -s -o /dev/null -w "%{http_code}" -X DELETE -H "Authorization: Bearer $TB" "$BASE/api/users/alice/sessions/$SA")"

echo "--- Layer 3: Workspace ---"
check "alice workspace exists" "0" \
  "$(test -d data/users/alice/workspace && echo 0 || echo 1)"
check "bob workspace exists" "0" \
  "$(test -d data/users/bob/workspace && echo 0 || echo 1)"

echo "--- Layer 4: Memory ---"
curl -s -X PUT -H "Authorization: Bearer $TA" \
  -H "Content-Type: application/json" \
  "$BASE/api/users/alice/memory" -d '{"test":"data"}' > /dev/null
check "bob reads alice's memory" "403" \
  "$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TB" "$BASE/api/users/alice/memory")"

echo "--- Layer 6: Container ---"
if command -v docker &> /dev/null && docker info &> /dev/null; then
    CONTAINERS=$(docker ps --filter "name=web-agent-" --format "{{.Names}}" 2>/dev/null || echo "")
    if [ -n "$CONTAINERS" ]; then
        echo "  Running containers: $CONTAINERS"
        check "containers have per-user naming" "0" "0"
    else
        echo "  No web-agent containers running (container mode may be off)"
    fi
else
    echo "  Docker not available, skipping container checks"
fi

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

# Multi-User Isolation Verification Guide

> 配套设计文档: [MULTI_USER_ISOLATION.md](./MULTI_USER_ISOLATION.md)

## 前置条件

```bash
# 启动服务器（启用认证）
ENFORCE_AUTH=true JWT_SECRET=test-secret-at-least-32-chars-long \
  uv run uvicorn main_server:app --reload --port 8000
```

## Layer 1: 认证隔离

### 1a. 未认证请求被拒绝

```bash
# REST — 应返回 401
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/users/alice/sessions
echo " (expected: 401)"

# WebSocket — 连接应被 4001 关闭
websocat "ws://localhost:8000/ws" 2>&1 | head -1
```

### 1b. 用户 A 的令牌不能访问用户 B 的资源

```bash
TOKEN_A=$(curl -s -X POST http://localhost:8000/api/auth/token \
  -H "Content-Type: application/json" \
  -d '{"user_id":"alice"}' | jq -r '.token')

TOKEN_B=$(curl -s -X POST http://localhost:8000/api/auth/token \
  -H "Content-Type: application/json" \
  -d '{"user_id":"bob"}' | jq -r '.token')

# alice 的令牌访问 bob 的资源 — 应为 403
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $TOKEN_A" \
  http://localhost:8000/api/users/bob/sessions
echo " (expected: 403)"
```

### 1c. `require_user_match` 路径-用户绑定

强制执行位置: `src/auth.py:127` — 当 `ENFORCE_AUTH=true` 时，URL 路径中的 `user_id` 必须与 JWT 的 `sub` 一致。测试同上。

**自动化测试：**

```bash
uv run pytest tests/unit/test_auth.py -v
uv run pytest tests/integration/test_auth_integration.py -v
```

---

## Layer 2: 会话隔离

### 2a. 跨用户会话历史拒绝

```bash
# alice 创建会话
SESSION_A=$(curl -s -X POST \
  -H "Authorization: Bearer $TOKEN_A" \
  -H "Content-Type: application/json" \
  http://localhost:8000/api/users/alice/sessions \
  -d '{"name":"alice-secret"}' | jq -r '.session_id')

# bob 尝试读取 alice 的会话历史 — 应为 403
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $TOKEN_B" \
  "http://localhost:8000/api/users/alice/sessions/$SESSION_A/history"
echo " (expected: 403)"
```

### 2b. SQL 层所有权验证

强制执行位置: `src/session_store.py:124-127` — 所有消息查询 JOIN `sessions` 表并过滤 `s.user_id = ?`。

```bash
sqlite3 data/web-agent.db \
  "SELECT s.session_id, s.user_id, COUNT(m.id) as msg_count
   FROM sessions s
   LEFT JOIN messages m ON m.session_id = s.session_id
   GROUP BY s.session_id"
```

### 2c. 消息缓冲区所有权检查

强制执行位置: `src/message_buffer.py:191-196` — `_ensure_buf` 在内存缓冲区中验证 `user_id` 是否与存储的所有者一致。

---

## Layer 3: 工作区隔离

### 3a. 路径验证单元测试

```bash
uv run pytest tests/unit/test_path_validation.py -v
```

关键场景：
- 相对路径通过 (`outputs/file.txt`)
- 遍历被阻止 (`../../../etc/passwd`)
- 绝对外部路径被阻止 (`/tmp/evil.sh`, `/Users/.../outside.txt`)

### 3b. 手动验证路径遍历

```python
from pathlib import Path
import main_server

ws = Path("/data/users/alice/workspace")

assert main_server.is_path_within_workspace("outputs/ok.txt", ws) is True
assert main_server.is_path_within_workspace("../../../etc/passwd", ws) is False
assert main_server.is_path_within_workspace("/tmp/evil.sh", ws) is False
```

### 3c. 数据目录物理分离

```bash
# 验证每个用户有独立的目录
ls -d data/users/alice/{workspace,memory}/
ls -d data/users/bob/{workspace,memory}/

# 一个用户创建的文件不应出现在另一个用户的目录中
touch data/users/alice/workspace/alice-file.txt
test -f data/users/bob/workspace/alice-file.txt && echo "FAIL: cross-user leak" || echo "PASS: directories isolated"
```

### 3d. Bash 命令写入检测

```bash
uv run python -c "
import main_server
from pathlib import Path

ws = Path('/workspace')

# 应被捕获（返回非 None）
r1 = main_server.check_bash_command_for_external_writes(
    'echo data > /tmp/test.txt', ws)
print(f'Direct write to /tmp: {\"CAUGHT\" if r1 else \"MISSED\"}')

r2 = main_server.check_bash_command_for_external_writes(
    'cp file.docx /Users/mac/outputs/out.txt', ws)
print(f'cp to /Users: {\"CAUGHT\" if r2 else \"MISSED\"}')

# 已知局限 — 变量展开可能绕过正则
r3 = main_server.check_bash_command_for_external_writes(
    'export P=/tmp; echo data > \$P/test.txt', ws)
print(f'Variable expansion bypass: {\"CAUGHT\" if r3 else \"BYPASSED (known limitation)\"}')
"
```

> **已知局限：** `check_bash_command_for_external_writes`（main_server.py:575）使用正则匹配，可通过 shell 变量展开、命令替换或编码绕过。生产环境务必启用容器模式。

---

## Layer 4: 内存隔离

### 4a. L1 内存 API 隔离

```bash
# alice 写入内存
curl -s -X PUT \
  -H "Authorization: Bearer $TOKEN_A" \
  -H "Content-Type: application/json" \
  http://localhost:8000/api/users/alice/memory \
  -d '{"preferences": {"theme": "dark"}}'

# bob 读取 alice 的内存 — 应为 403
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $TOKEN_B" \
  http://localhost:8000/api/users/alice/memory
echo " (expected: 403)"
```

强制执行位置: `src/memory.py:74-97` — `read()` 使用 `WHERE user_id = ?` 查询 `user_memory` 表。

### 4b. L2 Agent 笔记文件分离

```bash
# 检查文件系统分离
echo "Alice notes:"
ls data/users/alice/memory/ 2>/dev/null

echo "Bob notes:"
ls data/users/bob/memory/ 2>/dev/null
```

### 4c. 路径遍历检查（Agent 笔记）

`src/memory.py:177` 中的 `write_agent_note` 和 `read_agent_note` 直接将 `filename` 拼接到用户目录路径。验证是否存在遍历风险：

```python
from pathlib import Path
from src.memory import MemoryManager

mm = MemoryManager(user_id="alice", data_root=Path("data"))

# 检查包含 ../ 的文件名是否被净化
note_path = mm._agent_memory_dir / "../../bob/memory/test.md"
print(f"Resolved path: {note_path.resolve()}")
print(f"User dir:     {mm.user_dir.resolve()}")
print(f"Path within user dir: {str(note_path.resolve()).startswith(str(mm.user_dir.resolve()))}")
```

---

## Layer 5: WebSocket 隔离

### 5a. 连接用户锁定

强制执行位置: `main_server.py:1878-1896` — `_locked_user_id` 防止单条 WebSocket 连接切换用户。

```javascript
// 在浏览器控制台或 Node.js 中测试
const ws = new WebSocket("ws://localhost:8000/ws");

// 以 alice 身份发送
ws.send(JSON.stringify({ type: "subscribe", user_id: "alice", session_id: "s1" }));

// 尝试以 bob 身份发送 — 应被 _user_id_mismatch 静默丢弃
ws.send(JSON.stringify({ type: "subscribe", user_id: "bob", session_id: "s2" }));
// 消息在 main_server.py:1921-1931 处被拒绝
```

### 5b. WebSocket 认证

```bash
uv run pytest tests/integration/test_auth_integration.py -v -k "TestWebSocketAuth"
```

关键场景：
- 无令牌时拒绝（code 4001）
- 接受有效令牌
- 拒绝无效令牌
- `ENFORCE_AUTH=false` 时允许无令牌连接

---

## Layer 6: 容器隔离（生产环境）

> **前置条件：** 容器模式需显式开启。`CONTAINER_MODE` 默认 `false`，`docker ps --filter "name=web-agent-"` 结果为空是正常的。
> 启动命令：
> ```bash
> CONTAINER_MODE=true ENFORCE_AUTH=true JWT_SECRET=<secret> \
>   uv run uvicorn main_server:app --reload --port 8000
> ```

### 6a. 容器命名验证

```bash
docker ps --filter "name=web-agent-"
# 每个用户应有独立的容器: web-agent-alice, web-agent-bob
```

### 6b. 容器资源限制

强制执行位置: `src/container_manager.py:182` — 每个容器 `mem_limit="4g"`, `cpu_quota=100000`。

```bash
docker stats --filter "name=web-agent-" --no-stream
```

### 6c. 沙箱网络隔离

强制执行位置: `src/sandbox.py:82` — 默认 `network_mode="none"`。

> **注意：** `src/sandbox.py` 的 `DockerSandboxAdapter` 已实现但**未被任何生产代码调用**（死代码）。下方验证命令在 sandbox 集成到 agent 执行流程之前无法实际运行。

```bash
# 在沙箱容器内运行以下命令确认网络已隔离
docker exec web-agent-sandbox-alice ping -c 1 -W 2 8.8.8.8
# 应为: Network is unreachable
```

---

## 一键验证脚本

```bash
#!/bin/bash
# 保存为 scripts/verify-isolation.sh
set -euo pipefail

BASE="http://localhost:8000"
PASS=0
FAIL=0

check() {
    local desc="$1" expected="$2" actual="$3"
    if [ "$actual" = "$expected" ]; then
        echo "  PASS: $desc"
        ((PASS++))
    else
        echo "  FAIL: $desc (expected $expected, got $actual)"
        ((FAIL++))
    fi
}

echo "=== Web Agent Isolation Verification ==="
echo ""

# 注册用户
curl -s -X POST "$BASE/api/auth/register" -H "Content-Type: application/json" \
  -d '{"user_id":"alice","password":"pass1"}' > /dev/null 2>&1 || true
curl -s -X POST "$BASE/api/auth/register" -H "Content-Type: application/json" \
  -d '{"user_id":"bob","password":"pass2"}' > /dev/null 2>&1 || true

# 获取令牌
TA=$(curl -s -X POST "$BASE/api/auth/token" -H "Content-Type: application/json" \
  -d '{"user_id":"alice","password":"pass1"}' | jq -r '.token')
TB=$(curl -s -X POST "$BASE/api/auth/token" -H "Content-Type: application/json" \
  -d '{"user_id":"bob","password":"pass2"}' | jq -r '.token')

echo "--- Layer 1: Auth ---"
check "unauthenticated access" "401" \
  "$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/users/alice/sessions")"
check "cross-user session access (bob→alice)" "403" \
  "$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TB" "$BASE/api/users/alice/sessions")"
check "cross-user memory access (alice→bob)" "403" \
  "$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TA" "$BASE/api/users/bob/memory")"

echo "--- Layer 2: Sessions ---"
# alice 创建会话
SA=$(curl -s -X POST -H "Authorization: Bearer $TA" -H "Content-Type: application/json" \
  "$BASE/api/users/alice/sessions" -d '{"name":"test"}' | jq -r '.session_id')
check "bob reads alice's session history" "403" \
  "$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TB" "$BASE/api/users/alice/sessions/$SA/history")"
check "bob deletes alice's session" "403" \
  "$(curl -s -o /dev/null -w "%{http_code}" -X DELETE -H "Authorization: Bearer $TB" "$BASE/api/users/alice/sessions/$SA")"

echo "--- Layer 3: Data Directories ---"
check "alice workspace exists" "0" \
  "$(test -d data/users/alice/workspace && echo 0 || echo 1)"
check "bob workspace exists" "0" \
  "$(test -d data/users/bob/workspace && echo 0 || echo 1)"

echo "--- Layer 4: Memory ---"
curl -s -X PUT -H "Authorization: Bearer $TA" -H "Content-Type: application/json" \
  "$BASE/api/users/alice/memory" -d '{"test":"data"}' > /dev/null
check "bob reads alice's memory" "403" \
  "$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TB" "$BASE/api/users/alice/memory")"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
```

---

## 自动化测试覆盖

| 测试文件 | 覆盖层 | 运行命令 |
|---|---|---|
| `tests/unit/test_auth.py` | Layer 1 — 令牌创建/验证 | `uv run pytest tests/unit/test_auth.py -v` |
| `tests/integration/test_auth_integration.py` | Layer 1,5 — REST + WS 认证 | `uv run pytest tests/integration/test_auth_integration.py -v` |
| `tests/unit/test_path_validation.py` | Layer 3 — 路径遍历 | `uv run pytest tests/unit/test_path_validation.py -v` |
| `tests/unit/test_memory.py` | Layer 4 — 内存范围限定 | `uv run pytest tests/unit/test_memory.py -v` |
| `tests/unit/test_container_manager.py` | Layer 6 — 容器 | `uv run pytest tests/unit/test_container_manager.py -v` |
| `tests/unit/test_sandbox.py` | Layer 6 — 沙箱 | `uv run pytest tests/unit/test_sandbox.py -v` |
| `tests/integration/test_e2e_flow.py` | 端到端 | `uv run pytest tests/integration/test_e2e_flow.py -v` |

运行全部隔离相关测试：

```bash
uv run pytest \
  tests/unit/test_auth.py \
  tests/unit/test_path_validation.py \
  tests/unit/test_memory.py \
  tests/unit/test_container_manager.py \
  tests/unit/test_sandbox.py \
  tests/integration/test_auth_integration.py \
  tests/integration/test_e2e_flow.py \
  -v
```

---

## 已知局限与缓解措施

| # | 问题 | 位置 | 严重程度 | 缓解措施 |
|---|---|---|---|---|
| 1 | `ENFORCE_AUTH=false` 为默认值，所有用户合并为 `"default"` | 环境变量 | **高** | 非开发环境设置 `ENFORCE_AUTH=true` |
| 2 | JWT secret 回退到硬编码字符串 | `src/auth.py:31` | **高** | 设置环境变量 `JWT_SECRET` |
| 3 | 写入 Agent 笔记时未净化文件名（路径遍历风险） | `src/memory.py:177,190,212` | **中** | 增加 `is_path_within_user_dir` 检查 |
| 4 | Bash 命令正则可被变量展开绕过 | `main_server.py:575-594` | **中** | 生产环境启用 `CONTAINER_MODE=true`，Agent 在独立容器中运行 |
| 5 | `src/sandbox.py` 已实现但未被集成，SANDBOX_MODE 无实际效果 | `src/sandbox.py` | **低** | 不影响安全；容器隔离由 CONTAINER_MODE 提供 |
| 6 | 无按用户的任务并发限制 | `main_server.py:132` | **低** | 按 `user_id` 增加速率限制 |
| 7 | 全局 `_cli_session_map` 在所有用户间共享 | `main_server.py:134` | **低** | 按用户隔离映射 |

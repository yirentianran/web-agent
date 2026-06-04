# Security Regression Testing Design

## Context

Web Agent 的安全机制已在 defense-in-depth 设计中定义了三层防御。最近 `d2b302b` 提交完成了 Phase 1 的主要迁移（httpOnly cookie、CSRF 辅助函数、安全响应头、速率限制中间件）。但安全审计发现了 7 个需要修复的漏洞。本设计为每个漏洞建立 TDD 风格的测试，先发现问题再修复。

## Approach: TDD 漏洞修复

每个漏洞独立处理：写失败测试 → 验证失败 → 修复代码 → 测试通过。按严重程度排序，HIGH 优先。

## Test File Organization

```
tests/
├── unit/
│   ├── test_csrf.py                 # 新增 — Gap 1
│   ├── test_ws_auth.py              # 新增 — Gap 2+3
│   ├── test_rate_limiting.py        # 新增 — Gap 4
│   ├── test_mcp_credential_store.py # 新增 — Gap 5
│   ├── test_auth_messages.py        # 新增 — Gap 6
│   └── test_input_validation.py     # 新增 — Gap 7
├── integration/
│   ├── test_csrf_integration.py     # 新增 — Gap 1
│   ├── test_ws_security.py          # 新增 — Gap 2+3
│   └── test_rate_limit_integration.py # 新增 — Gap 4
└── security/
    └── audit_script.py              # 新增 — 渗透测试聚合脚本
```

复用已有 `conftest.py` 模式（SDK mock、tmp_path、TestClient）。

---

## Gap 1: CSRF 保护未接入路由 [HIGH]

**文件:** `src/auth.py:190-208`, `main_server.py`

**问题:** `verify_csrf()` 已实现但未在任何路由中作为依赖调用。

### 单元测试 (`tests/unit/test_csrf.py`)

- `verify_csrf()` 对 GET/HEAD/OPTIONS 请求跳过验证
- `verify_csrf()` 缺少 `X-CSRF-Token` header 时返回错误
- `verify_csrf()` header 与 `csrf_token` cookie 不匹配时返回错误
- `verify_csrf()` header 与 cookie 匹配时通过
- `ENFORCE_AUTH=false` 时跳过验证
- CSRF token 生成使用 `secrets.token_hex(32)` 且长度 >= 64

### 集成测试 (`tests/integration/test_csrf_integration.py`)

- `POST /api/users/{id}/sessions` 不带 CSRF header → 403
- `DELETE /api/users/{id}/sessions/{sid}` 不带 CSRF header → 403
- `POST /api/users/{id}/upload` 不带 CSRF header → 403
- 带合法 CSRF token 的状态变更请求 → 正常通过
- 带错误 CSRF token 的状态变更请求 → 403

### 修复

在 `main_server.py` 中将 `Depends(verify_csrf)` 加入所有非安全方法端点依赖。

---

## Gap 2: WebSocket user_id 不匹配未强制拒绝 [HIGH]

**文件:** `main_server.py:2839-2841`

**问题:** WS 消息中 user_id 与 token 不匹配时仅记录日志，不拒绝消息。

### 单元测试 (`tests/unit/test_ws_auth.py`)

- `ENFORCE_AUTH=true` 时，WS 消息 user_id 与 token sub 不匹配 → 拒绝，返回 error
- `ENFORCE_AUTH=false` 时，不匹配仅记录日志，消息正常处理
- token 缺失 → WS 关闭 (code=4001)
- token 无效/过期 → WS 关闭

### 集成测试 (`tests/integration/test_ws_security.py`)

- 以用户 A 连接 WS，发送 `{user_id: "B", ...}` → 被拒绝
- 以用户 A 连接 WS，发送 `{user_id: "A", ...}` → 正常处理
- 无 token 连接 WS → 连接关闭
- 伪造 token 连接 WS → 连接关闭

### 修复

在 WS 消息处理循环中，`_user_id_mismatch` 为 True 且 `ENFORCE_AUTH=true` 时，发送错误并 `continue`。

---

## Gap 3: Agent Server WebSocket 无认证 [HIGH]

**文件:** `agent_server.py:557-558`, `container_manager.py`

**问题:** Agent server WS 直接 accept 无任何认证。容器内 localhost 可达但缺纵深防御。

### 单元测试 (扩展 `tests/unit/test_ws_auth.py`)

- Agent server 启动时生成内部共享 secret
- 无 `X-Agent-Token` header → 连接拒绝
- 错误 token → 连接拒绝

### 集成测试 (扩展 `tests/integration/test_ws_security.py`)

- 无 token 连接 agent server WS → 拒绝
- 错误 token → 拒绝
- 正确 token → 正常建立

### 修复

Agent server 从环境变量读取 `AGENT_SECRET`，WS 连接校验 `X-Agent-Token` header。Container manager 启动容器时注入共享 secret。

---

## Gap 4: 速率限制仅覆盖认证端点 [MEDIUM]

**文件:** `main_server.py:166-176`

**问题:** 30+ 端点中只有 auth 两个有限速。

### 单元测试 (`tests/unit/test_rate_limiting.py`)

- slowapi Limiter 正确初始化并挂载
- 默认限速配置对所有端点生效
- 限速 key 使用 `get_remote_address`

### 集成测试 (`tests/integration/test_rate_limit_integration.py`)

- 连续快速创建 session → 超阈值后返回 429
- 连续上传文件 → 超阈值后返回 429
- 连续发送 WS 消息 → 超阈值后拒绝
- 429 响应包含 `Retry-After` header
- 认证端点已有限速不变

### 修复

添加全局默认限速（60 req/min per IP），文件上传和 session 创建端点设置更严格限速。WS 消息频率扩展 `ToolCallRateLimiter` 模式。

---

## Gap 5: MCP 凭证明文存储 [MEDIUM]

**文件:** `src/mcp_store.py:75-76, 127-128`

**问题:** MCP headers 和 env vars 以明文 JSON 存入 SQLite。

### 单元测试 (`tests/unit/test_mcp_credential_store.py`)

- 存储时 `headers` 和 `env` 中的值被加密
- 读取时解密恢复原始值
- 加密密钥从 `MCP_ENCRYPTION_KEY` 环境变量读取
- 密钥缺失时降级到明文 + 警告
- 已存储明文数据向后兼容
- DB 文件中存储密文

### 修复

在 `mcp_store.py` 中使用 `cryptography.fernet` 对敏感字段对称加密。密钥通过环境变量注入。

---

## Gap 6: ACCOUNT_DISABLED 泄露用户存在 [LOW]

**文件:** `main_server.py:5871-5872`

**问题:** 禁用账户返回特定消息，可枚举已注册用户。

### 单元测试 (`tests/unit/test_auth_messages.py`)

- 禁用账户登录 → "Invalid credentials"（与不存在一致）
- 错误密码 → "Invalid credentials"
- 不存在用户 → "Invalid credentials"
- 三种情况响应一致，无法区分
- 响应时间差异不显著

### 修复

将禁用账户检查移到密码验证之前，统一返回 "Invalid credentials"。

---

## Gap 7: Pydantic 模型缺少输入长度验证 [LOW]

**文件:** `main_server.py:3697, 4677-4685, 4787`

**问题:** 请求模型字段无 `min_length`/`max_length` 约束。

### 单元测试 (`tests/unit/test_input_validation.py`)

- `TitleUpdate.title` 超过 500 字符 → 422
- `TaskCreateRequest.subject` 超过 200 字符 → 422
- `SkillFeedbackRequest.comment` 超过 5000 字符 → 422
- 空字符串 required 字段 → 422
- 正常长度 → 通过

### 修复

在 Pydantic model 字段中添加 `Field(min_length=1, max_length=N)` 约束。

---

## 渗透测试聚合脚本 (`tests/security/audit_script.py`)

独立脚本，模拟攻击者视角。可脱离 pytest 运行：

```
uv run python tests/security/audit_script.py
```

场景覆盖：
1. 无 token 访问受保护 API → 应 401
2. 伪造 token 访问 → 应 401
3. 用户 A 访问用户 B 的 session → 应 403
4. 不带 CSRF token 的 POST 请求 → 应 403
5. 路径遍历文件上传 → 应被拦截
6. 超大文件上传 → 应 413
7. 快速连续请求触发限速 → 应 429
8. WebSocket 跨用户消息 → 应被拒绝

输出通过/失败报告，可用作 CI 安全门禁。

## Verification

1. 运行 `uv run pytest tests/unit/ -v` — 新增单元测试全部通过
2. 运行 `uv run pytest tests/integration/ -v` — 新增集成测试全部通过
3. 运行 `uv run python tests/security/audit_script.py` — 所有攻击场景被防线拦截
4. 手动确认：浏览器中登录两个不同用户，尝试 CSRF 和 WS 跨用户场景
5. 回归检查：已有测试套件全部通过

# 跨用户数据隔离漏洞 — 完整安全审计报告

## 背景

两个用户 yguo 和 xiangyan 在同一 web agent 实例中，yguo 可以在 chatarea 正常看到欢迎页面，但能向 xiangyan 的 session 发送消息，导致数据串扰。经四轮深入分析，共发现 **25 个安全漏洞**，涉及 WebSocket、REST API、文件系统、技能管理、MCP 配置、密钥管理等。

核心根因有三：
1. `ENFORCE_AUTH` 默认关闭，auth 层变为空操作
2. 多处代码在处理 `session_id` 时未交叉验证其是否属于当前用户
3. 多个全局共享资源（MCP 服务器、API 密钥、技能配置）缺乏用户级隔离

---

## 技能端点权限矩阵（当前 vs 要求）

用户确认：Shared Skill 和 Skill Evolution（candidates、versions、review）均需 admin 权限。

| 端点 | 行号 | 当前权限 | 要求权限 | 状态 |
|------|------|---------|---------|------|
| `GET /api/shared-skills` | 3003 | **无认证** | `require_admin` | ❌ 需修复 |
| `POST /api/shared-skills/upload` | 3238 | `require_admin` | `require_admin` | ✅ |
| `DELETE /api/shared-skills/{name}` | 3294 | `require_admin` | `require_admin` | ✅ |
| `POST /api/skills/{name}/evolve-agent` | 4019 | `get_current_user` | `require_admin` | ❌ 需修复 |
| `GET /api/skills/{name}/evolve-status/{id}` | 4076 | `get_current_user` | `require_admin` | ❌ 需修复 |
| `GET /api/admin/skills/evolution-candidates` | 4167 | `require_admin` | `require_admin` | ✅ |
| `POST /api/skills/{name}/activate-version` | 3808 | `get_current_user` | `require_admin` | ❌ 需修复 |
| `POST /api/skills/{name}/rollback` | 3829 | `get_current_user` | `require_admin` | ❌ 需修复 |
| `GET /api/skills/{name}/version` | 4229 | **无认证** | `require_admin` | ❌ 需修复 |
| `GET /api/skills/{name}/version/{v}` | 4260 | **无认证** | `require_admin` | ❌ 需修复 |
| `GET /api/skills/{name}/version-files/{n}` | 4118 | `get_current_user` | `require_admin` | ❌ 需修复 |
| `GET /api/skills/{name}/version-file/{n}` | 4147 | `get_current_user` | `require_admin` | ❌ 需修复 |

**修复方式**：将上述 9 个 ❌ 端点的依赖从 `get_current_user`（或无认证）统一改为 `Depends(require_admin)`。

## 漏洞清单

### A. 严重漏洞（可直接泄露或篡改其他用户数据）

#### 漏洞 1：fork_session 可复制任意用户完整对话历史

- **文件**：`main_server.py:2857`
- **代码**：`history = await buffer.get_history(session_id)`
- **攻击**：用户 A 调用 `POST /api/users/user-a/sessions/{user-b的session_id}/fork`，`verify_path_user` 通过但不校验 session 归属，`get_history` 不加 user_id 返回完整对话并复制到攻击者名下
- **修复**：`await buffer.get_history(session_id, user_id=user_id)`

#### 漏洞 2：cancel_session 可取消任意用户的运行中 Agent 任务

- **文件**：`main_server.py:2824-2843`
- **攻击**：用户 A 调用 `POST /api/users/user-a/sessions/{user-b的session_id}/cancel`，任务取消通过全局 `f"task_{session_id}"` 键值，无所有权检查
- **修复**：添加 session 归属验证

#### 漏洞 3：add_message 所有调用均不传 user_id，所有权检查被完全跳过

- **文件**：`main_server.py` 12+ 处 + `message_buffer.py:224`
- **代码**：
  ```python
  # _ensure_buf 的所有权检查
  if user_id is not None and stored_user is not None and user_id != stored_user:
      raise ValueError(...)
  # user_id=None → 整个 if 块不执行
  ```
- **调用点**：行 1342, 1722, 1842, 1861, 1872, 1895, 1914, 1948, 2338, 2341, 2439, 2859
- **修复**：`add_message` 的 `user_id` 改为必填参数

#### 漏洞 4：WebSocket "answer" 消息可劫持其他用户的 AskUserQuestion

- **文件**：`main_server.py:2088, 2124, 2217, 2418`
- **代码**：
  ```python
  future = pending_answers.get(sid)  # 仅按 session_id 查找
  if future and not future.done():
      future.set_result(answers)  # 无 user_id 校验！
  ```
- **攻击**：用户 B 知道用户 A 的 session_id，发送 `type:"answer"` 消息，回答用户 A agent 的待处理问题。注释明确写了 "始终处理答案，无论会话是什么"
- **修复**：`pending_answers` 存储时记录 user_id，回答时验证 `_locked_user_id` 匹配

#### 漏洞 5：9 个技能端点权限不足（Shared Skill / Evolution / Version / Review 均需 admin）

- **文件**：`main_server.py`
- **当前状态**：
  - 3 个端点**无任何认证**：`GET /api/shared-skills` (3003)、`GET /api/skills/{name}/version` (4229)、`GET /api/skills/{name}/version/{v}` (4260)
  - 6 个端点仅需 `get_current_user`：`evolve-agent` (4019)、`evolve-status` (4076)、`activate-version` (3808)、`rollback` (3829)、`version-files` (4118)、`version-file` (4147)
- **要求**：全部 9 个端点改为 `require_admin`
- **修复**：依赖注入统一改为 `Depends(require_admin)`

#### 漏洞 6：JWT_SECRET 硬编码默认值

- **文件**：`src/auth.py:31`
- **代码**：`JWT_SECRET = _SECRET or "dev-secret-change-in-production-use-at-least-32-chars"`
- **攻击**：任何读过源码的人都能用此密钥伪造任意用户（包括 admin）的 JWT
- **修复**：生产环境必须设置 `JWT_SECRET` 环境变量；启动时若为默认值则拒绝启动

### B. 高危漏洞

#### 漏洞 7：MCP 服务器运行时访问不做用户隔离

- **文件**：`main_server.py:864-889, 1052-1079`
- **代码**：`load_mcp_config_sync()` 查询所有 MCP 服务器，`build_allowed_tools()` 全部注入每个用户的 agent session
- **攻击**：`mcp_servers` 表有 `access` 列但运行时从不读取。所有 MCP 服务器的 env 变量（可能含 API key）在所有用户间共享
- **修复**：运行时按 `access` 列过滤或实现用户级 MCP 配置

#### 漏洞 8：共享 ANTHROPIC_AUTH_TOKEN 注入所有用户 SDK 子进程

- **文件**：`main_server.py:1158-1163`
- **代码**：
  ```python
  api_key = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")
  if api_key:
      sdk_env["ANTHROPIC_AUTH_TOKEN"] = api_key
  ```
- **攻击**：任何用户的 agent 可通过 Bash 工具运行 `echo $ANTHROPIC_AUTH_TOKEN` 提取共享 API key
- **修复**：不在子进程环境中传递 API key，或使用每用户独立 key

#### 漏洞 9：get_session_files 查询 uploads/generated_files 无 user_id 过滤

- **文件**：`main_server.py:2711-2713`
- **代码**：`SELECT ... FROM uploads WHERE session_id = ?`（无 `AND user_id = ?`）
- **攻击**：用户 A 知道用户 B 的 session_id 后，可列出该 session 所有文件
- **修复**：SQL 添加 `AND user_id = ?`

#### 漏洞 10：技能分析接口暴露 user_id 到 recent_comments

- **文件**：`skill_feedback.py:88-97, 245`
- **攻击**：任何已认证用户可查看任意技能的 recent_comments 中谁提交了评价
- **修复**：从公开 API 移除 `user_id` 字段

#### 漏洞 11：技能建议端点暴露跨用户反馈数据

- **文件**：`main_server.py:3697` → `skill_feedback.py:341-381`
- **攻击**：任何已认证用户可获取所有用户对任意技能的反馈
- **修复**：添加管理员权限检查

#### 漏洞 12：ENFORCE_AUTH 默认关闭，dev 模式全部端点无保护

- **文件**：`src/auth.py:18, 107-108, 132-133`
- **影响**：全部 REST 端点、文件下载/上传/删除均失去保护
- **修复**：生产部署确保 `ENFORCE_AUTH=true`

#### 漏洞 13：注册端点无认证依赖 + 无频率限制

- **文件**：`main_server.py:4710`
- **攻击**：任何人可无限注册账户；可通过 409 响应枚举已有用户
- **修复**：添加频率限制；生产模式添加邀请码或管理员审批

### C. 中危漏洞

#### 漏洞 14：session state 直接绕过所有权写入 buffer

- **文件**：`main_server.py:2326-2328`
- **代码**：`buf_state["done"] = False; buf_state["state"] = "running"`
- **修复**：先调用 `buffer._ensure_buf(session_id, user_id=_locked_user_id)`

#### 漏洞 15：create_session 不关联 user_id

- **文件**：`main_server.py:2620`
- **代码**：`buffer._ensure_buf(session_id)` 无 user_id
- **修复**：传入 `user_id=user_id`

#### 漏洞 16：_write_db_async 直接 INSERT 无所有权检查

- **文件**：`message_buffer.py:152-167`
- **修复**：写入前 `SELECT user_id FROM sessions WHERE session_id = ?` 验证

#### 漏洞 17：_delete_db_async 仅按 session_id 删除

- **文件**：`message_buffer.py:107-121`
- **修复**：添加 session 所有权验证

#### 漏洞 18：WebSocket subscribe 循环 get_history 不传 user_id

- **文件**：`main_server.py:2458, 2485`
- **修复**：传入 `user_id=_locked_user_id`

#### 漏洞 19：_can_use_tool_for_session 签名无 user_id

- **文件**：`main_server.py:1333`
- **修复**：签名加入 `user_id` 参数

#### 漏洞 20：Agent 文件扫描器可读取服务器项目根目录

- **文件**：`main_server.py:1792-1823`
- **代码**：扫描 `Path(__file__).parent` 和 `Path.home()` 查找最近修改的文件并移入用户 workspace
- **攻击**：Agent 任务期间在共享目录创建的文件可能被移入用户 workspace 并被下载
- **修复**：限制扫描范围为用户 workspace 目录

#### 漏洞 21：WebSocket 断开不清理 pending_answers

- **文件**：`main_server.py:2532-2563`
- **攻击**：用户断开后，待处理 Future 在 300 秒超时前仍可被新连接解决
- **修复**：断开时取消 pending_answers 中属于该连接的 Future

#### 漏洞 22：用户反馈数据存储在全局可读 JSONL 文件

- **文件**：`skill_feedback.py` → `DATA_ROOT/training/skill-feedback/` 目录
- **攻击**：文件模式下的反馈数据存储在全局目录，无文件级权限隔离
- **修复**：确保 DB 模式优先；文件模式限制目录权限

### D. 低危漏洞

#### 漏洞 23：_emit_synthetic_state_change_if_missing 不传 user_id

- **文件**：`main_server.py:195-197`
- **修复**：传入 `user_id`

#### 漏洞 24：前端无客户端 session 所有权校验

- **文件**：`App.tsx:342-343`
- **修复**：可选

#### 漏洞 25：session_id 仅用 12 位十六进制（48 位熵），增加碰撞风险

- **文件**：`main_server.py:2619`
- **代码**：`f"sess_{uuid.uuid4().hex[:12]}"`
- **修复**：增加到 24 位（96 位熵）

---

## 漏洞汇总表

| # | 严重度 | 漏洞简述 | 文件 |
|---|--------|---------|------|
| 1 | 严重 | fork_session 可复制任意用户会话 | main_server.py |
| 2 | 严重 | cancel_session 可取消任意用户 Agent | main_server.py |
| 3 | 严重 | add_message 全线不传 user_id（12+ 处） | main_server.py + message_buffer.py |
| 4 | 严重 | WS answer 消息可劫持 AskUserQuestion | main_server.py |
| 5 | 严重 | 9 个技能端点权限不足，需 admin | main_server.py |
| 6 | 严重 | JWT_SECRET 硬编码默认值可伪造令牌 | src/auth.py |
| 7 | 高 | MCP 服务器+密钥在所有用户间共享 | main_server.py |
| 8 | 高 | ANTHROPIC_AUTH_TOKEN 泄露给所有用户 | main_server.py |
| 9 | 高 | get_session_files SQL 无 user_id 过滤 | main_server.py |
| 10 | 高 | 技能分析泄露 user_id | skill_feedback.py |
| 11 | 高 | 技能建议暴露跨用户数据 | main_server.py + skill_feedback.py |
| 12 | 高 | ENFORCE_AUTH 默认关闭 | src/auth.py |
| 13 | 高 | 注册端点无认证+无频率限制 | main_server.py |
| 14 | 中 | session state 直接绕过所有权写入 | main_server.py |
| 15 | 中 | create_session 不关联 user_id | main_server.py |
| 16 | 中 | _write_db_async 无所有权检查 | message_buffer.py |
| 17 | 中 | _delete_db_async 仅按 session_id 删除 | message_buffer.py |
| 18 | 中 | WS subscribe get_history 不传 user_id | main_server.py |
| 19 | 中 | _can_use_tool_for_session 无 user_id | main_server.py |
| 20 | 中 | Agent 扫描器可读服务器根目录 | main_server.py |
| 21 | 中 | WS 断开不清理 pending_answers | main_server.py |
| 22 | 中 | 反馈数据存全局可读 JSONL 文件 | skill_feedback.py |
| 23 | 低 | synthetic_state_change 不传 user_id | main_server.py |
| 24 | 低 | 前端无客户端 session 校验 | App.tsx |
| 25 | 低 | session_id 熵不足（48 位） | main_server.py |

---

## 需修改的文件

| 文件 | 修改项（漏洞编号） |
|------|-------------------|
| `main_server.py` | 1-5, 7-10, 12, 14-17, 20-23, 25, 27 |
| `message_buffer.py` | 3, 18, 19 |
| `src/auth.py` | 6, 13 |
| `src/skill_feedback.py` | 11, 24 |

---

## 验证方案

### 优先修复（P0 — 直接堵漏）

1. 漏洞 6：`JWT_SECRET` 启动时检查，默认值拒绝启动
2. 漏洞 3：`add_message` user_id 改为必填
3. 漏洞 1：`fork_session` 传 user_id
4. 漏洞 4：`pending_answers` 加 user_id 校验
5. 漏洞 2：`cancel_session` 加 session 归属验证

### 自动化测试

- 用户 A fork 用户 B 的 session → 403
- 用户 A cancel 用户 B 的 session → 403
- 用户 A 向用户 B session 发 answer → 被拒绝
- 用户 A 查看用户 B session files → 403
- 非 admin 激活技能版本 → 403
- 未认证访问技能版本 → 401
- buffer.add_message 不传 user_id → 抛出异常

### 手动验证

```bash
ENFORCE_AUTH=true JWT_SECRET=test-secret-32-chars-minimum \
  uv run uvicorn main_server:app --port 8000

# 注册两个用户并获取 token
# xiangyan 创建 session 并触发 AskUserQuestion
# yguo 尝试：
#   1. 向 xiangyan 的 session 发聊天消息 → 应被拒绝
#   2. 回答 xiangyan 的待处理问题 → 应被拒绝
#   3. fork xiangyan 的 session → 应 403
#   4. cancel xiangyan 的任务 → 应 403
#   5. 查看 xiangyan 的 session 文件 → 应 403
```

### 回归测试

```bash
uv run pytest
cd frontend && npm test
```

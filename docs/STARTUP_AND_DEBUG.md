# Web Agent Platform — 启动与调试指南

> 最后更新：2026-04-13（第 2 次更新：前后端合并）

---

## 1. 环境要求

| 组件 | 最低版本 | 推荐版本 | 说明 |
|------|----------|----------|------|
| Python | 3.12 | 3.12+ | `pyproject.toml` 要求 `>=3.12` |
| Node.js | 18 | 20+ | 前端使用 React 19 + Vite 6 |
| Docker | 24+ | 最新稳定版 | 容器模式需要 Docker Engine + Compose v2 |
| 内存 | 4 GB | 8 GB+ | 生产环境需要 |

### 验证环境

```bash
python3 --version    # >= 3.12
node --version       # >= 18
docker --version     # Docker Engine
docker compose version  # Compose v2
```

---

## 2. 快速开始（本地开发，推荐）

### 2.1 配置环境变量

```bash
# 复制模板
cp .env.example .env

# 编辑 .env，至少设置以下内容：
# ANTHROPIC_API_KEY=sk-xxx          # 必填：API 密钥
# ANTHROPIC_BASE_URL=https://...    # 可选：自定义 API 端点（如百炼）
# MODEL=claude-sonnet-4-6           # 可选：模型名称
# LOG_LEVEL=debug                   # 开发模式建议设为 debug
# DATA_ROOT=./data                  # 数据目录
```

### 2.2 一键启动（推荐）

```bash
# 一条命令同时启动前后端，支持 HMR 热更新
./start-dev.sh
```

输出示例：
```
Starting backend (uvicorn :8000) + frontend (vite :3000)...
[API] INFO:     Uvicorn running on http://0.0.0.0:8000
[WEB]   ➜  Local:   http://localhost:3000/
```

脚本会自动：
- 激活 `.venv` 虚拟环境
- 安装前端依赖（如未安装）
- 并行启动后端和前端

### 2.3 分步启动（备选）

如果需要分别控制前后端：

```bash
# 终端 1 — 启动后端
source .venv/bin/activate
uvicorn main_server:app --host 0.0.0.0 --port 8000 --reload

# 终端 2 — 启动前端
cd frontend && npm run dev
```

### 2.4 访问

| 服务 | 地址 | 说明 |
|------|------|------|
| 前端（开发） | http://localhost:3000 | Vite 开发服务器，支持 HMR |
| 后端 API（开发） | http://localhost:8000 | FastAPI |
| 健康检查 | http://localhost:8000/health | 返回 `{"status": "ok"}` |
| WebSocket | ws://localhost:8000/ws | 实时通信 |
| API 文档 | http://localhost:8000/docs | Swagger UI（自动生） |

### 2.5 登录

打开 http://localhost:3000，输入任意 user ID（开发模式无需真实认证），即可开始使用。

---

## 2b. 生产模式（单服务器）

生产模式下前端构建为静态文件，FastAPI 统一服务，只需一个端口：

```bash
# 1. 构建前端到 src/static/
cd frontend && npm run build:deploy

# 2. 启动单服务器
PROD=true uvicorn main_server:app --host 0.0.0.0 --port 8000
```

| 服务 | 地址 | 说明 |
|------|------|------|
| 前端 + API | http://localhost:8000 | 同源，无需 CORS |
| SPA 路由 | http://localhost:8000/sessions | 自动 fallback 到 index.html |

---

## 3. Docker 方式启动

### 3.1 开发模式

```bash
docker compose up --build
```

- 后端端口：8000
- 热重载：已启用（源文件 bind mount）
- 认证：已禁用（`ENFORCE_AUTH=false`）
- 数据卷：`./data` 挂载到容器

### 3.2 生产模式

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build
```

- 添加 Nginx 反向代理（端口 80）
- 后端 2 个 worker，无热重载
- 认证：已启用（`ENFORCE_AUTH=true`）
- 健康检查：已配置
- 资源限制：2 CPU / 4GB 内存

### 3.3 仅启动特定服务

```bash
docker compose up --build main-server   # 仅后端
docker compose up --build nginx          # 仅 nginx（需要先启动 main-server）
```

### 3.4 查看日志

```bash
docker compose logs -f main-server   # 实时查看后端日志
docker compose logs --tail=100        # 查看最近 100 行
```

---

## 4. 运行测试

```bash
source .venv/bin/activate

# 全部测试
pytest tests/ -v

# 单元测试
pytest tests/unit/ -v

# 集成测试
pytest tests/integration/ -v

# 带覆盖率
pytest --cov=src --cov-report=term-missing tests/

# 运行单个测试文件
pytest tests/unit/test_skill_evolution.py -v

# 运行特定测试用例
pytest tests/unit/test_skill_evolution.py::TestShouldEvolve::test_low_ratings_trigger_evolve -v
```

当前状态：**235 测试全部通过**。

---

## 5. 代码质量工具

### 5.1 Lint

```bash
source .venv/bin/activate
ruff check .              # 检查
ruff check . --fix         # 自动修复
ruff check src/           # 仅检查 src/
```

配置规则：E, F, W, I, N, UP, B, SIM，行宽 120。

### 5.2 类型检查

```bash
source .venv/bin/activate
mypy .                    # 全项目严格模式
mypy src/skill_evolution.py  # 单文件
```

### 5.3 前端类型检查

```bash
cd frontend
npx tsc --noEmit          # TypeScript 编译检查
```

---

## 6. 调试指南

### 6.1 后端调试

#### 查看后端日志

后端日志直接输出到启动 uvicorn 的终端。如果后端在后台运行或日志已滚动，可以通过以下方式查看：

```bash
# 本地开发：日志直接输出到运行 uvicorn 的终端

# Docker 方式：
docker compose logs -f main-server
docker compose logs --tail=100 main-server

# 如果日志写入文件（生产环境）：
tail -f data/logs/app/app.log
```

#### 启用详细日志

在 `.env` 中设置：
```
LOG_LEVEL=debug
```

这会输出 WebSocket 帧、Agent 任务创建/完成事件、消息缓冲区状态等详细信息。

#### WebSocket 帧调试

在 `.env` 中设置 `LOG_LEVEL=debug` 后，后端会输出每条 WebSocket 消息的内容：

```
DEBUG: < TEXT '{"type": "chat", "message": "..."}'
DEBUG: > TEXT '{"type": "system", "subtype": "progress", ...}'
```

关键日志模式：
- `Agent task created` — 新任务启动
- `Agent task completed with N messages in X.Xs` — 任务完成
- `buf_done=..., buf_state=..., buf_msg_count=...` — 消息缓冲区状态
- `Drained N messages` — 从缓冲区推送给前端的消息数

#### 消息缓冲区竞态条件（已修复）

**历史问题**：第二及后续消息无响应。

**原因**：Agent 任务在 WebSocket handler 的 drain 循环开始之前就已标记 `done=True`，导致 drain 循环立即退出。

**修复**：
1. 创建新任务时重置缓冲区：`done=False`, `state="running"`, 清空消息队列
2. Drain 循环总是先推送现有消息，再检查 `is_done()`

**如遇类似症状**，检查 `src/message_buffer.py` 中的 `mark_done()` 和 drain 循环逻辑。

#### 使用 Python 调试器

```python
# 在需要断点的位置插入
import pdb; pdb.set_trace()

# 或 Python 3.7+
breakpoint()
```

然后直接运行 uvicorn（不要用 `--reload`，会与 pdb 冲突）：
```bash
uvicorn main_server:app --host 0.0.0.0 --port 8000
```

#### VS Code 调试配置

创建 `.vscode/launch.json`：
```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Python: FastAPI",
      "type": "debugpy",
      "request": "launch",
      "module": "uvicorn",
      "args": ["main_server:app", "--host", "0.0.0.0", "--port", "8000"],
      "env": {
        "ANTHROPIC_API_KEY": "${env:ANTHROPIC_API_KEY}",
        "LOG_LEVEL": "debug"
      },
      "justMyCode": false
    }
  ]
}
```

### 6.2 前端调试

#### React DevTools

安装 [React Developer Tools](https://chromewebstore.google.com/detail/react-developer-tools/fmkadmapgofadopljbjfkapdkoienihi) 浏览器扩展。

#### Vite 调试模式

```bash
BACKEND_PORT=8000 npx vite --port 3000 --debug
```

#### 网络请求调试

浏览器 DevTools → Network → 筛选 `ws` 查看 WebSocket 帧。

### 6.3 WebSocket 调试

#### 手动测试

```bash
# 使用 wscat 工具
npm install -g wscat
wscat -c ws://localhost:8000/ws

# 连接后发送消息
> {"type": "chat", "message": "hello", "user_id": "test"}
```

#### Python 端调试

在 `main_server.py` 的 WebSocket handler 中添加日志：
```python
import logging
logger = logging.getLogger(__name__)

# 在 WebSocket handler 中
logger.debug("WS message received: %s", data)
```

### 6.4 容器调试

```bash
# 查看容器状态
docker ps -a

# 进入容器 shell
docker exec -it <container_name> /bin/bash

# 查看容器日志
docker logs <container_name>

# 查看容器资源使用
docker stats
```

### 6.5 数据库/文件调试

```bash
# 查看用户数据
ls -la data/users/

# 查看会话历史
cat data/users/<user_id>/claude-data/sessions/*.jsonl | head -20

# 查看记忆文件
cat data/users/<user_id>/memory.json

# 查看技能反馈
ls -la data/training/skill-feedback/

# 查看日志
ls -la data/logs/audit/
ls -la data/logs/app/
ls -la data/logs/agent/
```

---

## 7. 已知问题与限制

### 7.1 会话上下文不保持

**现象**：选择历史会话后发送新消息，AI 不会"记住"之前的对话内容，每次都是新的上下文。

**原因**：Claude CLI 使用内部数字 ID（如 `74981.json`）管理会话，存储在 `~/.claude/sessions/`。我们的自定义 session ID 与 Claude CLI 的 ID 不映射，因此无法通过 `--resume` 恢复对话上下文。

**当前行为**：消息历史通过消息缓冲区的磁盘 JSONL 文件加载到 UI 显示，但每次新消息都会启动一个全新的 Agent 调用，不携带之前的对话上下文。

**影响范围**：页面刷新后重新选择历史会话时，能看到之前的消息记录，但新消息是独立对话。

### 7.2 `--reload` 不会总是拾取路由变更

**现象**：在 `main_server.py` 中添加新的路由后，`uvicorn --reload` 有时不会重新加载，导致新端点返回 404。

**解决**：遇到新路由 404 时，kill 所有 uvicorn 进程后重新启动，不加 `--reload` 标志：
```bash
pkill -f uvicorn
uvicorn main_server:app --host 0.0.0.0 --port 8000
```

---

## 8. 常见问题排查

### 7.1 后端启动失败

| 现象 | 原因 | 解决 |
|------|------|------|
| `ModuleNotFoundError: No module named 'fastapi'` | 未激活 venv 或未安装依赖 | `source .venv/bin/activate && pip install -e ".[dev]"` |
| `ModuleNotFoundError: No module named 'dotenv'` | 缺少 python-dotenv | `pip install python-dotenv` |
| `OSError: [Errno 48] Address already in use` | 端口 8000 被占用 | `lsof -i :8000` 查看并 `kill` 进程 |
| `KeyError: 'ANTHROPIC_API_KEY'` | 未设置环境变量 | 创建 `.env` 文件并设置 API key |

### 7.2 前端启动失败

| 现象 | 原因 | 解决 |
|------|------|------|
| `Cannot find module 'react'` | 未运行 npm install | `cd frontend && npm install` |
| `ECONNREFUSED 127.0.0.1:8000` | 后端未启动 | 先启动后端服务或运行 `./start-dev.sh` |
| TypeScript 报错 | 类型不匹配 | `npx tsc --noEmit` 查看详细错误 |

### 7.3 WebSocket 连接失败

| 现象 | 原因 | 解决 |
|------|------|------|
| WebSocket 连接立即关闭 | 后端未启动或端口不匹配 | 确认后端在 8000 运行 |
| 消息无法送达 | session_id 不匹配 | 检查前端 sessionId 状态 |
| 403 错误 | 认证未通过 | 开发模式确保 `ENFORCE_AUTH=false` |

### 7.4 Docker 问题

| 现象 | 原因 | 解决 |
|------|------|------|
| `Cannot connect to Docker daemon` | Docker 未运行 | `open -a Docker` (macOS) |
| 容器启动后崩溃 | API key 未传入 | 检查 `docker compose config` 中的环境变量 |
| 端口冲突 | 其他服务占用端口 | `docker compose down && docker compose up` |

---

## 8. 项目架构速查

### 8.1 服务角色

```
开发模式（两个进程，Vite HMR）：
┌─────────────┐     HTTP/WS     ┌──────────────┐     SDK Subprocess     ┌───────────────┐
│   Frontend  │ ──────────────→ │  Main Server │ ────────────────────→  │ Agent Server  │
│  (port 3000)│     proxy       │  (port 8000) │                        │  (container)  │
│  React+TS   │ ←────────────── │  FastAPI     │ ←────────────────────  │  FastAPI      │
└─────────────┘     WS/REST     └──────────────┘     WebSocket Bridge   └───────────────┘

生产模式（单服务器）：
┌──────────────────────────────────────────────┐
│              Main Server :8000               │
│  ┌────────────┐         ┌──────────────────┐ │
│  │  /api/*    │         │  Static Files    │ │
│  │  /ws       │         │  (src/static/)   │ │
│  │  FastAPI   │         │  React SPA       │ │
│  └────────────┘         └──────────────────┘ │
└──────────────────────────────────────────────┘
```

- **Frontend（开发）**：React 19 + TypeScript + Vite，端口 3000
- **Frontend（生产）**：构建为 `src/static/` 静态文件
- **Main Server**：FastAPI，端口 8000，管理 REST API + WebSocket 桥接 + 静态文件
- **Agent Server**：FastAPI + Claude SDK 子进程，运行在用户容器中，端口 8000

### 8.2 关键文件

| 文件 | 职责 |
|------|------|
| `main_server.py` | 主服务器：REST API、WebSocket 桥接、容器管理、会话管理 |
| `agent_server.py` | 容器内 Agent 服务：SDK 子进程、技能加载、记忆注入 |
| `src/message_buffer.py` | 消息缓冲：内存+磁盘双层，支持会话恢复 |
| `src/websocket_bridge.py` | WebSocket 双向代理：浏览器 ↔ Agent |
| `src/auth.py` | JWT 认证 |
| `src/admin_auth.py` | Admin 角色强制 |
| `src/memory.py` | 记忆系统：L1 平台记忆 + L2 Agent Notes |

### 8.3 数据目录

```
data/
├── users/
│   └── {user_id}/
│       ├── workspace/                    # 工作区
│       ├── claude-data/sessions/         # 会话 JSONL 历史
│       ├── claude-data/uploads/          # 上传的文件
│       ├── memory.json                   # 平台记忆
│       ├── memory/                       # Agent Notes (Markdown)
│       └── skills/                       # 个人技能
├── shared-skills/                        # 共享技能
├── training/                             # 训练数据
│   ├── qa/
│   ├── skill-feedback/
│   ├── preferences/
│   ├── skill_outcomes/
│   └── corrections/
├── logs/                                 # 日志
│   ├── audit/                            # L1 审计日志
│   ├── app/                              # L2 应用日志
│   └── agent/                            # L3 Agent 日志
└── .msg-buffer/                          # 消息缓冲（磁盘备份）
```

---

## 9. 开发工作流

### 推荐方式（一键）

```bash
./start-dev.sh
```

### 传统方式（多终端）

```bash
# 1. 启动后端（终端 1）
source .venv/bin/activate
uvicorn main_server:app --host 0.0.0.0 --port 8000 --reload

# 2. 启动前端（终端 2）
cd frontend && npm run dev

# 3. 运行测试（终端 3）
source .venv/bin/activate
pytest tests/unit/test_skill_evolution.py -v

# 4. 代码检查（终端 3）
ruff check . && mypy .
npx tsc --noEmit -p frontend/
```

### 生产构建

```bash
cd frontend && npm run build:deploy
PROD=true uvicorn main_server:app --host 0.0.0.0 --port 8000
```

---

## 10. E2E 验证脚本

项目提供了自动化验证脚本：

```bash
./scripts/verify-e2e.sh [PORT]
```

该脚本会：
1. 检查 Python/Node.js/Docker 前置条件
2. 运行全部单元测试
3. 启动后端服务
4. 执行 REST 健康检查
5. 输出手动浏览器测试指引

---

## 11. 生产部署检查清单

- [ ] `.env` 中设置 `ANTHROPIC_API_KEY`（生产密钥，勿用开发密钥）
- [ ] `JWT_SECRET` 更换为至少 32 字符的随机密钥
- [ ] `ADMIN_USER_IDS` 设置实际管理员用户 ID
- [ ] `ENFORCE_AUTH=true`
- [ ] `ENFORCE_ADMIN=true`
- [ ] `CONTAINER_MODE=true`（如果启用容器隔离）
- [ ] `LOG_LEVEL=info`（生产环境不要 debug）
- [ ] Dockerfile 添加非 root 用户（当前以 root 运行，见 `docs/PROJECT_STATUS.md`）
- [ ] Nginx 配置 TLS/HTTPS（当前仅 HTTP）
- [ ] 定期执行日志清理（`POST /api/admin/logs/cleanup`）
- [ ] 备份 `data/` 目录

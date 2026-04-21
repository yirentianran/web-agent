# 继续实现计划 — Phase 1.3 完成 + Phase 2.0 待开始

## 现状分析

### **已完成 (Phase 1 全部完成)**
- **项目脚手架**: pyproject.toml, Dockerfile, docker-compose.yml, .env.example
- **后端核心**: main_server.py (已迁移到真实 claude_agent_sdk)
- **共享模块**: message_buffer.py, models.py, cost.py, container_manager.py, hooks/
- **前端骨架**: React + Vite, 组件 (Sidebar, ChatArea, MessageBubble, InputBar), hooks (useWebSocket), types
- **REST API**: 全部 20+ 端点已实现 (sessions, files, skills, memory, MCP, feedback, health)
- **SDK 迁移**: mock SDK → claude_agent_sdk v0.1.58, ClaudeSDKClient 集成完成
- **Vite 代理配置**: 支持 BACKEND_PORT 环境变量 (默认 8000, Docker 开发用 8080) — `frontend/vite.config.ts` 已修改
- **单元测试**: 51 个测试全部通过 (test_message_buffer.py 20+, test_main_server.py 25+)
- **集成测试**: 13 个测试全部通过 (tests/integration/test_e2e_flow.py) — WS 基础、历史回放、Answer 流程、Session 生命周期、成本追踪
- **验证脚本**: `scripts/verify-e2e.sh` — 自动检查前提 → 运行测试 → 启动后端 → REST 验证 → 手动浏览器指引

### 已完成验证
- 51 个单元测试 + 13 个集成测试 = **64 个测试全部通过**
- `scripts/verify-e2e.sh` 运行成功，所有自动化检查通过
- Vite 代理验证: 前端 `http://localhost:3001` → 后端 `http://localhost:8000` 代理正常工作
- `claude` CLI 已安装 (v2.1.104)

### 待完成 (Phase 1.3 手动验证)
1. **设置 ANTHROPIC_API_KEY**: 创建 `.env` 文件，设置 `ANTHROPIC_API_KEY_DEFAULT` 环境变量
2. **浏览器端到端测试**: 打开 `http://localhost:3000` → 新建 session → 发送消息 → 验证 agent 完整生命周期

### 待实现 (Phase 2.0)
7. **Docker 容器编排**: container_manager.py 已编写但未集成到 main_server.py
8. **WebSocket 桥接**: main_server ↔ agent_server 跨容器通信
9. **用户认证**: JWT / OAuth 接入
10. **资源管控**: CPU/内存/磁盘配额

## Phase 2.0 实施计划

### Step 6: JWT 认证模块
- **文件**: `src/auth.py` (新), `pyproject.toml` (添加 `PyJWT` 依赖)
- Token 创建/验证、FastAPI dependency、`JWT_SECRET` 环境变量

### Step 7: 容器集成到主服务
- **文件**: `main_server.py` (修改)
- `CONTAINER_MODE` 环境变量切换 Phase 1/2 模式
- 集成 `container_manager.py`，新增容器管理 REST 端点

### Step 8: WebSocket 桥接
- **文件**: `src/websocket_bridge.py` (新)
- 双向 WS 代理：浏览器 ↔ main_server ↔ container 内 agent_server
- 含重连逻辑 (指数退避)

### Step 9: REST + WS 接入认证
- **文件**: `main_server.py` (修改)
- `/api/auth/token` 端点、WS 支持 `?token=` 查询参数、`ENFORCE_AUTH` 开关

### Step 10: 资源管理
- **文件**: `src/resource_manager.py` (新)
- CPU/内存/磁盘监控、配额检查、`GET /api/admin/resources`

### Step 11-12: Docker Compose 更新 + 前端认证
- **文件**: `docker-compose.yml`, `frontend/src/hooks/useWebSocket.ts`, `frontend/src/App.tsx`
- 共享网络、shared-skills 卷、WS URL 传 token

### Step 13-14: 测试
- `tests/unit/test_auth.py`, `test_container_manager.py`, `test_websocket_bridge.py`, `test_resource_manager.py`
- `tests/integration/test_container_orchestration.py`

## 风险
- **MEDIUM**: `ANTHROPIC_API_KEY` 未配置 — 需要先设置 `.env` 才能手动测试
- **MEDIUM**: WebSocket 桥接连接断开 — 桥接模块需实现指数退避重连
- **LOW**: Docker daemon 未运行 — Phase 2.0 测试需要 Docker Desktop

## 建议
- 先设置 `ANTHROPIC_API_KEY` 完成 Phase 1.3 手动浏览器验证
- 确认端到端流程跑通后，再开始 Phase 2.0 容器编排

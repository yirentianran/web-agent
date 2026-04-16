# Web Agent Platform - Full Implementation Plan

> Phase 1: 9/9 | Phase 2: 6/6 | Phase 3: 7/7 | Phase 4: 3/3 | Phase 5: 3/5 (OAuth deferred)
> Last updated: 2026-04-13 | Tests: 235 passing

---

## Phase 6: Frontend/Backend Merge (COMPLETE)

| Step | File | Status | 说明 |
|------|------|--------|------|
| 6.1 | `frontend/vite.config.ts` | Done | `build.outDir` → `../src/static` |
| 6.2 | `main_server.py` | Done | `StaticFiles` 挂载 + SPA fallback 路由 + 生产模式关闭 CORS |
| 6.3 | `frontend/package.json` | Done | `concurrently` 依赖 + `build:deploy` 脚本 |
| 6.4 | `start-dev.sh` | Done | 一键启动开发模式（双进程 + HMR） |
| 6.5 | `.gitignore` | Done | 添加 `src/static/` |
| 6.6 | `docs/STARTUP_AND_DEBUG.md` | Done | 同步更新启动指南 |

**开发模式**：`./start-dev.sh` → 前端 `:3000` + 后端 `:8000`（保留 HMR）
**生产模式**：`npm run build:deploy` + `PROD=true uvicorn` → 单端口 `:8000`

---

---

## Phase 1: Quick Wins (COMPLETE)

| # | Item | Status | Files Changed |
|---|------|--------|---------------|
| P1.1 | Tool Output Truncation | Done | `src/truncation.py`, `main_server.py` |
| P1.2 | File Upload Validation | Done | `src/file_validation.py`, `main_server.py` |
| P1.3 | Session Disk Cleanup | Done | `src/session_cleanup.py`, `main_server.py` |
| P1.4 | Frontend Markdown Rendering | Done | `frontend/src/components/MarkdownRenderer.tsx`, `MessageBubble.tsx` |
| P1.5 | Frontend File Cards | Done | `frontend/src/components/FileCards.tsx`, `MessageBubble.tsx` |
| P1.6 | Responsive Breakpoints | Done | `frontend/src/styles/global.css` |
| P1.7 | Frontend Todo Panel | Done | `frontend/src/components/TodoPanel.tsx`, `ChatArea.tsx` |
| P1.8 | Session Auto-Title Generation | Done | `frontend/src/App.tsx`, `main_server.py` |
| P1.9 | Admin Role Enforcement | Done | `src/admin_auth.py`, `src/auth.py`, `main_server.py` |

---

## Phase 2: L1-L4 Logging Infrastructure (COMPLETE)

| Step | Module | Status | Tests |
|------|--------|--------|-------|
| 2.1 | `src/audit_logger.py` | Done | 10 tests — append-only, query, hash-chain tamper detection |
| 2.2 | `src/app_logger.py` | Done | 5 tests — JSON format, extra fields, exception handling |
| 2.3 | `src/agent_logger.py` | Done | 8 tests — tool call/result/end-session, truncation |
| 2.4 | `src/container_manager.py` | Done (config) | L4 via docker logging driver (json-file, max-size 10MB, max-file 3) |
| 2.5 | 集成 | Done | audit log 接入 admin_auth.py、auth.py、run_agent_task |
| 2.6 | `src/log_cleanup.py` | Done | 5 tests — retention eviction, empty dir cleanup |
| 2.7 | Admin API | Done | 4 integration tests — audit-log query, log cleanup trigger |

**New files**: `src/audit_logger.py`, `src/app_logger.py`, `src/agent_logger.py`, `src/log_cleanup.py`
**Modified files**: `src/admin_auth.py`, `src/auth.py`, `main_server.py`
**Tests**: 32 new (28 unit + 4 integration)

---

## Phase 3: Agent Memory, Sub-Agents, Skill Evolution (COMPLETE)

| Step | Module | Status | Tests |
|------|--------|--------|-------|
| 3.1 | `src/memory.py` | Done | 10 tests — CRUD, deep_merge, corrupted file handling |
| 3.2 | `main_server.py` Memory API | Done (已存在，增强) | 原有测试通过 |
| 3.3 | `src/memory.py` L2 扩展 | Done | Agent notes CRUD + load_agent_memory_for_prompt |
| 3.4 | `src/sub_agent.py` | Done | 12 tests — 任务生命周期、依赖追踪、状态机 |
| 3.5 | `main_server.py` Sub-Agent API | Done | POST/PATCH/GET/DELETE `/api/users/{user_id}/tasks` |
| 3.6 | `src/skill_feedback.py` | Done | 8 tests — 反馈收集、分析、改进建议 |
| 3.7 | `frontend/` 记忆面板 | Done | `MemoryPanel.tsx` + CSS，platform/agent tabs |

**New files**: `src/memory.py`, `src/sub_agent.py`, `src/skill_feedback.py`, `frontend/src/components/MemoryPanel.tsx`
**Modified files**: `main_server.py` (新增 15+ 端点)
**Tests**: 34 new unit tests

---

## Phase 4: Production Infrastructure (COMPLETE)

| Step | File | Status | 说明 |
|------|------|--------|------|
| 4.1 | `nginx/conf.d/default.conf` | Done | 反向代理、WebSocket proxy、rate limiting、安全头 |
| 4.1 | `nginx/Dockerfile` | Done | Nginx Alpine 镜像 |
| 4.2 | `docker-compose.prod.yml` | Done | 健康检查、restart policies、resource limits |
| 4.3 | K8s manifests | 可选 | 需要时创建 Deployment/Service/Ingress |

**Tests**: 基础设施配置，手动验证

## Phase 5: Remaining Features (COMPLETE)

| Step | File | Status | 测试数 |
|------|------|--------|--------|
| 5.1 | `src/sandbox.py` | Done | 11 tests — 沙箱协议 + Docker 实现 + Stub 降级 |
| 5.3 | `main_server.py` fork_session | Done | 3 tests — 会话分叉 + 元数据复制 |
| 5.5 | OAuth | **Deferred** | 当前 JWT + ENFORCE_AUTH 足够 |
| 5.2 | Admin Dashboard | **Deferred** | 需要实际部署后按需开发 |
| 5.4 | MCP 网络隔离 | **Deferred** | 依赖容器模式启用 |
| 5.6 | `src/skill_evolution.py` | Done | 12 tests — LLM 技能进化 + 版本管理 |
| 5.7 | `src/ab_testing.py` | Done | 11 tests — 哈希分流 + 胜出判定 |
| 5.8 | `frontend/AskUserQuestionCard.tsx` | Done | 问题卡片 + 选项按钮 + WebSocket 回答 |
| 5.9 | `frontend/SkillFeedbackWidget.tsx` | Done | 星评组件 + 评论提交 |

**New files**: `src/skill_evolution.py`, `src/ab_testing.py`, `frontend/src/components/AskUserQuestionCard.tsx`, `frontend/src/components/SkillFeedbackWidget.tsx`
**Modified files**: `main_server.py` (新增 7 端点 + 启动目录创建), `frontend/src/components/MessageBubble.tsx`, `frontend/src/components/ChatArea.tsx`, `frontend/src/App.tsx`, `frontend/src/lib/types.ts`, `frontend/src/styles/global.css`
**Tests**: 23 new (12 evolution + 11 A/B testing)
**Total Tests**: 235 passing

**Modified files**: `docker-compose.yml` (新增日志/沙箱环境变量)

---

## Phase Dependencies

```
Phase 1 (Quick Wins)           [COMPLETE]
    │
    ▼
Phase 2 (Logging Infrastructure)  Steps 2.1-2.4 parallel → 2.5 integrate → 2.6 cleanup
    │
    ▼
Phase 3 (Memory/Sub-Agents)       Steps 3.1-3.3 parallel → 3.4-3.5 sequential → 3.6-3.7
    │
    ▼
Phase 4 (Production Infra)        Steps 4.1-4.2 sequential, 4.3 optional
    │
    ▼
Phase 5 (Remaining Features)      5.1, 5.3 parallel → 5.2 → 5.4 → 5.5 (OAuth last)
    │
    ▼
Phase 6 (Frontend/Backend Merge)  Steps 6.1-6.4 parallel → 6.5-6.6 docs
```

**Phase 4 can proceed in parallel with Phase 2-3** (no hard dependency).
**Phase 5.5 (OAuth) can be deferred** — current JWT + ENFORCE_AUTH is sufficient for non-production.

---

## 剩余工作

- **OAuth 集成** (Phase 5.5): 替换当前 JWT user_id 为 OAuth2/OIDC，当需要多用户 SSO 时再做
- **Admin Dashboard** (Phase 5.2): 前端管理面板，需要实际部署后按需开发
- **MCP 网络隔离** (Phase 5.4): 依赖容器模式启用
- **K8s manifests** (Phase 4.3): 需要 Kubernetes 环境时添加
- **Nginx 生产模式** (Phase 4): 如果使用单服务器模式，Nginx 可省略，FastAPI 直接服务

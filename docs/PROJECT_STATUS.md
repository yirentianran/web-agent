# Web Agent — 架构完成度分析报告

> 分析时间：2026-04-13 | 测试：235 通过 | 对比基准：`docs/claude-agent-sdk-multi-user-architecture.md`
> 总体完成度：**~90%**（基础设施 100%，内容/示例 0%，安全加固 1 项缺失）

---

## 一、逐节对比：架构规范 vs 实际实现

| 架构章节 | 功能 | 状态 | 证据 | 差距 | 严重度 |
|---------|------|------|------|------|--------|
| **3.1** | Session 列表侧边栏 | 完成 | `Sidebar.tsx` — 会话列表、活跃状态点、折叠/展开 | 无 | — |
| **3.2** | Chat 区域 | 完成 | `ChatArea.tsx` — 消息列表、TodoPanel、SkillFeedbackWidget、状态栏 | 无 | — |
| **3.3** | 输入区 + 文件上传 | 完成 | `InputBar.tsx` — 文本输入、文件附加、文件 chips、自动缩放 | 无 | — |
| **3.4** | 文件管理卡片 | 完成 | `FileCards.tsx` — FileCard + FileCardList，上传/结果/错误状态 | 无 | — |
| **3.5** | Markdown 渲染 | 完成 | `MarkdownRenderer.tsx` — react-markdown + remark-gfm | 无 | — |
| **3.6** | 响应式布局 | 完成 | `global.css` — 3 断点 (767px, 1023px, 1024px) | 无 | — |
| **3.7** | Todo 面板 | 完成 | `TodoPanel.tsx` — 解析 TaskCreate/TaskUpdate 消息 | 无 | — |
| **3.8** | AskUserQuestion UI 卡片 | 完成 | `AskUserQuestionCard.tsx` — 问题块 + 选项按钮 + 提交 | 无 | — |
| **3.9** | 技能反馈组件 | 完成 | `SkillFeedbackWidget.tsx` — 5 星评分 + 评论 | 无 | — |
| **3.10** | 记忆面板 | **部分** | `MemoryPanel.tsx` 存在 | **未接入 App.tsx**，组件定义但未渲染 | 中 |
| **4.1** | JWT 认证 | 完成 | `src/auth.py` — create_token/verify_token, ENFORCE_AUTH | 无 | — |
| **4.2** | Admin 角色强制 | 完成 | `src/admin_auth.py` — require_admin, ENFORCE_ADMIN | 无 | — |
| **4.3** | SKILL.md 双层架构 | 完成 | `main_server.py` load_skills() — shared + personal 覆盖合并 | 代码实现完整 | — |
| **4.4** | SKILL.md 示例 | **未开始** | `data/shared-skills/` 为空 | 零个示例技能文件 | 低 |
| **5.1** | L1 平台记忆 (memory.json) | 完成 | `src/memory.py` — CRUD + deep_merge | 无 | — |
| **5.2** | L2 Agent 记忆 (Markdown) | 完成 | `src/memory.py` — agent notes CRUD + load_agent_memory_for_prompt() | 无 | — |
| **5.3** | 记忆注入系统提示词 | 完成 | `main_server.py` build_system_prompt() → load_memory() | 无 | — |
| **5.4** | 会话分叉 | 完成 | `main_server.py` fork endpoint (line 645) | 无 | — |
| **5.5.1** | 技能反馈收集 | 完成 | `src/skill_feedback.py` — SkillFeedbackManager | 无 | — |
| **5.5.2** | 技能进化 (LLM) | 完成 | `src/skill_evolution.py` — SkillEvolutionManager + Anthropic API | 需要 ANTHROPIC_API_KEY 运行时 | — |
| **5.5.3** | A/B 测试 | **部分** | `src/ab_testing.py` — SkillABTest | **record 端点空操作**，数据写入断开 | 中 |
| **5.5.4** | 训练数据目录 | 完成 | `data/training/` — qa/, skill-feedback/, preferences/, skill_outcomes/, corrections/ | corrections/ 无代码写入 | 低 |
| **5.5.5** | 6 阶段进化管道 | **部分** | 阶段 1-3、6 已实现 | **缺阶段 4** (admin 审批)、**缺阶段 5** (A/B 灰度)、**缺阶段 6** (全量替换) | 中 |
| **6.1** | MCP 注册表 | 完成 | `main_server.py` — load_mcp_config() + build_allowed_tools() | 无 | — |
| **6.2** | MCP Admin API | 完成 | 4 端点：list/register/unregister/toggle | **缺 toggle_tool** (单工具开关) | 低 |
| **6.3** | 容器 MCP 注入 | 完成 | `container_manager.py` — settings.json + MCP_CONFIG_JSON env | 无 | — |
| **7.1** | Agent Loop | 完成 | `main_server.py` run_agent_task() — ClaudeSDKClient 循环 | 无 | — |
| **7.2** | AskUserQuestion 拦截 | 完成 | `_can_use_tool_for_session()` — Future + 300s 超时 | 无 | — |
| **7.3** | 钩子系统 | 完成 | `src/hooks/` — pre_tool_use, post_tool_use, on_stop | 无 | — |
| **7.4** | 工具输出截断 | 完成 | `src/truncation.py` — head + summary | 无 | — |
| **7.5** | 会话管理 / 消息缓冲 | 完成 | `src/message_buffer.py` — 内存+磁盘双层、多消费者 | 无 | — |
| **7.6** | 子代理 | 完成 | `src/sub_agent.py` — 任务 CRUD + 依赖 + 状态机 | 无 | — |
| **7.7** | 成本追踪 | 完成 | `src/cost.py` — Claude + Qwen 多模型定价 | 无 | — |
| **7.8** | 容器会话管理 | 完成 | `container_manager.py` — create/pause/unpause/destroy | 无 | — |
| **7.9** | 断连续传 (Resume on Refresh) | 完成 | `message_buffer.py` — 磁盘+内存、replay:true | 无 | — |
| **7.10** | Agent 工作指示器 (Spinner) | **部分** | `ChatArea.tsx` — 静态 "Agent is working..." | **缺动态工具名更新**，无 SpinnerManager | 低 |
| **7.11** | 任务管理 (Todo List) | 完成 | `TodoPanel.tsx` + SubAgentManager + allowed_tools | 无 | — |
| **7.12.1** | L1 审计日志 | 完成 | `src/audit_logger.py` — 哈希链防篡改、查询、验证 | 无 | — |
| **7.12.2** | L2 应用日志 | 完成 | `src/app_logger.py` — JSON 格式、定时轮转 | 无 | — |
| **7.12.3** | L3 Agent 日志 | 完成 | `src/agent_logger.py` — 工具链计时、输出截断 | 无 | — |
| **7.12.4** | L4 容器日志 | 完成 | Docker logging driver (json-file, max 10MB, 3 files) | 无 | — |
| **7.12.5** | 日志清理 | 完成 | `src/log_cleanup.py` — 4 层保留期驱逐 | 无 | — |
| **8.1** | Main Server | 完成 | `main_server.py` — 1,466 行，45+ 端点 | 无 | — |
| **8.2** | ~~Container Agent Server~~ | **已删除** | `agent_server.py` — Phase 2+ 容器架构，当前未使用 |
| **8.3** | Dockerfile | **部分** | 35 行，python:3.12-slim, Claude CLI | **缺非 root USER** | **关键** |
| **9.1** | Docker Compose Dev | 完成 | `docker-compose.yml` — 热重载、环境变量 | **缺 MCP 服务** | 低 |
| **9.2** | Docker Compose Prod | 完成 | `docker-compose.prod.yml` — 健康检查、resource limits | **缺 TLS/HTTPS** | 中 |
| **9.3** | Nginx 反向代理 | 完成 | `nginx/conf.d/default.conf` — rate limiting, 安全头 | 无 | — |
| **10** | 审计技能示例 | **未开始** | `data/shared-skills/` 为空，`mcp-servers/` 为空 | 零个审计技能或 MCP 服务器 | 低 |
| **11** | 已知限制文档 | 完成 | 架构文档 11 节 | 纯文档，无需代码 | — |

---

## 二、后端模块清单 (22/22 已实现)

| 模块 | 行数 | 测试覆盖 | 状态 |
|------|------|----------|------|
| `src/models.py` | 199 | 间接 | 完成 |
| `src/auth.py` | 119 | 10 测试 | 完成 |
| `src/admin_auth.py` | 44 | 间接 | 完成 |
| `src/audit_logger.py` | 184 | 10 测试 | 完成 |
| `src/app_logger.py` | 105 | 5 测试 | 完成 |
| `src/agent_logger.py` | 142 | 8 测试 | 完成 |
| `src/log_cleanup.py` | 71 | 5 测试 | 完成 |
| `src/memory.py` | 143 | 10 测试 | 完成 |
| `src/sub_agent.py` | 157 | 12 测试 | 完成 |
| `src/skill_feedback.py` | 136 | 8 测试 | 完成 |
| `src/skill_evolution.py` | 237 | 12 测试 | 完成 |
| `src/ab_testing.py` | 135 | 11 测试 | 完成 |
| `src/sandbox.py` | 215 | 8 测试 | 完成 |
| `src/container_manager.py` | 222 | 间接 | 完成 |
| `src/resource_manager.py` | 146 | 间接 | 完成 |
| `src/message_buffer.py` | 168 | 间接 | 完成 |
| `src/websocket_bridge.py` | 121 | 4 测试 | 完成 |
| `src/truncation.py` | 33 | 5 测试 | 完成 |
| `src/file_validation.py` | 36 | 间接 | 完成 |
| `src/session_cleanup.py` | 77 | 4 测试 | 完成 |
| `src/cost.py` | 45 | 间接 | 完成 |
| `src/hooks/*` | 118 | 间接 | 完成 |

---

## 三、前端组件清单 (11/11 已创建, 10/11 已接入)

| 组件 | 文件 | 接入状态 | 说明 |
|------|------|----------|------|
| App | `App.tsx` | 主入口 | 登录、会话管理、WebSocket 接线 |
| Sidebar | `Sidebar.tsx` | 已接入 | 会话列表 |
| ChatArea | `ChatArea.tsx` | 已接入 | 消息展示 + 反馈组件 |
| InputBar | `InputBar.tsx` | 已接入 | 文本输入 + 文件上传 |
| MessageBubble | `MessageBubble.tsx` | 已接入 | 消息渲染 (含 AskUserQuestion 卡片) |
| MarkdownRenderer | `MarkdownRenderer.tsx` | 已接入 | Markdown 渲染 |
| FileCards | `FileCards.tsx` | 已接入 | 文件上传/结果卡片 |
| TodoPanel | `TodoPanel.tsx` | 已接入 | 任务列表 |
| MemoryPanel | `MemoryPanel.tsx` | **未接入** | 组件存在但 App.tsx 未导入 |
| AskUserQuestionCard | `AskUserQuestionCard.tsx` | 已接入 | 问题交互卡片 |
| SkillFeedbackWidget | `SkillFeedbackWidget.tsx` | 已接入 | 星评反馈组件 |

---

## 四、API 端点统计 (45+ REST + 1 WebSocket)

| 类别 | 端点数 | 路径示例 |
|------|--------|----------|
| 认证 | 1 | `POST /api/auth/token` |
| 会话管理 | 6 | `POST/GET/DELETE /api/users/{user_id}/sessions` |
| 文件管理 | 4 | `POST /api/users/{user_id}/upload` |
| 技能管理 | 5 | `GET/POST /api/users/{user_id}/skills` |
| 记忆系统 | 5 | `GET/PUT /api/users/{user_id}/memory` |
| 子代理任务 | 6 | `POST/GET/PATCH/DELETE /api/users/{user_id}/tasks` |
| 技能反馈 | 4 | `POST /api/skills/{skill_name}/feedback` |
| 技能进化 | 2 | `POST /api/skills/{skill_name}/evolve` |
| A/B 测试 | 3 | `POST/GET /api/skills/{skill_name}/ab-test` |
| MCP 管理 | 4 | `GET/POST/DELETE /api/admin/mcp-servers` |
| Admin | 8 | `GET /api/admin/containers` |
| 健康检查 | 1 | `GET /health` |
| WebSocket | 1 | `/ws` |

---

## 五、待处理工作 (按优先级)

### 5.1 关键 (Critical) — 必须修复

| # | 问题 | 位置 | 修复方案 | 工作量 |
|---|------|------|----------|--------|
| C1 | **Dockerfile 非 root 用户缺失** | `Dockerfile` | 添加 `RUN useradd -r agent && USER agent` | 2 行 |

### 5.2 高优先级 (High) — 代码审查发现

| # | 问题 | 位置 | 修复方案 | 工作量 |
|---|------|------|----------|--------|
| H1 | `record_ab_test_result` 端点空操作 | `main_server.py:1129` | 实现实际数据写入或返回 501 | 低 |
| H2 | 反馈组件硬编码 skillName="general" | `ChatArea.tsx:54` | 从 session result 中提取 skill_used | 中 |

### 5.3 中优先级 (Medium) — 功能缺失

| # | 问题 | 位置 | 修复方案 | 工作量 |
|---|------|------|----------|--------|
| M1 | MemoryPanel 组件未接入 | `App.tsx` | 添加导入和渲染 | 3 行 |
| M2 | 技能进化缺 admin 审批门 | `skill_evolution.py` | 添加 pending → approved 状态 | 中 |
| M3 | 技能版本无法自动提升为活跃 | `skill_evolution.py` | 添加 promote_version() 方法 | 低 |
| M4 | A/B 测试 recording 断开 | `main_server.py:1129` | 关联 session 中的 version 信息 | 中 |
| M5 | Prod 缺 TLS/HTTPS 配置 | `nginx/conf.d/default.conf` | 添加 Let's Encrypt / certbot 配置 | 中 |

### 5.4 低优先级 (Low) — 完善项

| # | 问题 | 说明 |
|---|------|------|
| L1 | 零个 SKILL.md 示例文件 | `data/shared-skills/` 为空，需编写 audit-pdf/excel/image 等示例 |
| L2 | 零个 MCP 服务器实现 | `mcp-servers/` 为空，需编写 PDF/Excel/OCR 等 MCP 服务 |
| L3 | `as any` 类型断言 | `MessageBubble.tsx:37` 改为 `as AskUserQuestionInput` |
| L4 | 无用 import/方法 | `main_server.py:1157` (dead glob), `ab_testing.py:12` (unused field) |
| L5 | Spinner 为静态文本 | 架构文档描述动态工具名 spinner，当前为 "Agent is working..." |
| L6 | E2E 自动化测试缺失 | 仅手动 verify 脚本，无 Playwright 测试 |
| L7 | MCP toggle_tool 端点缺失 | 架构文档 Section 6.5 描述的单个工具开关未实现 |
| L8 | corrections/ 目录无写入代码 | `data/training/corrections/` 存在但无 .diff 写入逻辑 |

### 5.5 已延期 (Deferred) — 主动决定

| 功能 | 原因 |
|------|------|
| OAuth 集成 | 当前 JWT + ENFORCE_AUTH 已满足需求 |
| Admin Dashboard 前端 | 需要部署后按需开发 |
| MCP 网络隔离 | 依赖容器模式启用 |
| K8s manifests | 暂无 Kubernetes 环境 |
| CONTAINER_MODE=true (prod) | 需要实际部署后启用 |

---

## 六、测试覆盖详情

| 测试文件 | 测试数 | 覆盖模块 |
|----------|--------|----------|
| `test_auth.py` | 10 | JWT 创建/验证/过期/角色 |
| `test_main_server.py` | ~30 | API 端点集成 |
| `test_message_buffer.py` | ~5 | 消息缓冲 + 磁盘回退 |
| `test_container_manager.py` | ~5 | Docker 容器生命周期 |
| `test_websocket_bridge.py` | 4 | WebSocket 双向代理 |
| `test_resource_manager.py` | ~5 | 资源监控 |
| `test_truncation.py` | 5 | 工具输出截断 |
| `test_file_validation.py` | ~3 | 文件上传验证 |
| `test_session_cleanup.py` | 4 | 会话磁盘清理 |
| `test_audit_logger.py` | 10 | 审计日志 + 哈希链 |
| `test_app_logger.py` | 5 | 应用日志 JSON 格式 |
| `test_agent_logger.py` | 8 | Agent 工具链日志 |
| `test_log_cleanup.py` | 5 | 日志保留期清理 |
| `test_memory.py` | 10 | 平台记忆 + Agent Notes |
| `test_skill_feedback.py` | 8 | 技能反馈分析 |
| `test_sub_agent.py` | 12 | 子代理任务状态机 |
| `test_sandbox.py` | 8 | 沙箱适配器协议 |
| `test_session_fork.py` | 3 | 会话分叉 |
| `test_skill_evolution.py` | 12 | 技能进化 + should_evolve |
| `test_ab_testing.py` | 11 | A/B 测试 + 胜出判定 |
| `test_auth_integration.py` | 14 | Admin 角色 + 审计日志集成 |
| `test_e2e_flow.py` | ~3 | 端到端流程 |
| **总计** | **235** | |

---

## 七、量化指标

| 指标 | 值 |
|------|-----|
| 后端 Python 模块 | 22 (src/ 20 + 顶层 2) |
| 前端 React 组件 | 11 |
| API 端点 | 45+ REST + 1 WebSocket |
| 单元测试文件 | 20 |
| 集成测试文件 | 2 |
| 通过测试数 | 235 |
| 后端代码行数 | ~3,500+ |
| 前端代码行数 | ~1,200+ |
| 测试代码行数 | ~2,000+ |
| 活跃用户 (data/) | 2 (default, yguo) |
| 会话缓冲文件 | 9 个 JSONL |
| 架构章节覆盖率 | 32/35 完成 (91%) |
| 后端模块覆盖率 | 22/22 实现 (100%) |
| 前端组件接入率 | 10/11 接入 (91%) |

---

## 八、结论

### 已完成的核心能力
1. **完整的多用户 Web Agent 平台基础设施** — 认证、授权、会话管理、文件管理
2. **L1-L4 四级日志体系** — 审计(防篡改) + 应用(JSON) + Agent(工具链) + 容器(Docker)
3. **分层记忆系统** — 平台 memory.json (deep_merge) + Agent Markdown Notes (自动注入)
4. **子代理任务管理** — 完整 CRUD + 依赖追踪 + 状态机
5. **技能反馈与进化** — RLHF 管道 (收集→分析→LLM 改写→版本管理)
6. **A/B 测试框架** — 哈希分流 + 统计胜出判定
7. **生产级部署配置** — Nginx + Docker Compose + 健康检查 + 资源限制
8. **AskUserQuestion 交互** — WebSocket 跨端问答完整实现

### 主要差距
1. **安全加固** (1 项 Critical)：Dockerfile 缺非 root 用户
2. **代码质量** (2 项 High)：AB test recording 空操作 + 反馈 skill 名硬编码
3. **内容/示例** (2 项 Low)：零个 SKILL.md 示例、零个 MCP 服务器实现
4. **流程完整性** (3 项 Medium)：进化管道缺审批/灰度/全量替换环节

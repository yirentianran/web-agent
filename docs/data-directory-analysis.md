# data/ 目录结构分析

> 2026-05-19 深度分析，含代码路径追溯与清理记录

## 目录

1. [磁盘结构](#一磁盘结构)
2. [存储架构](#二存储架构)
3. [目录创建代码路径](#三目录创建代码路径)
4. [发现的问题](#四发现的问题)
5. [数据清理记录](#五数据清理记录)

## 一、磁盘结构

```
data/
├── web-agent.db                   [3.3 MB]  主 SQLite 数据库（15 张表 + 2 FTS5 虚拟表）
├── web-agent.db-shm               [32 KB]   WAL 共享内存
├── web-agent.db-wal               [0 B]     已 checkpoint 清空
├── shared-skills/                 [8 条目, 29 文件]
│   ├── bank-confirmation-extractor/
│   ├── counterpart-analysis/
│   │   ├── SKILL.md
│   │   ├── skill-meta.json
│   │   ├── scripts/          (counterpart_analysis.py, generate_knowledge.py)
│   │   └── references/       (account-knowledge.md)
│   ├── depreciation-amortization-reconciliation/
│   ├── excel-merger/
│   │   ├── SKILL.md
│   │   ├── skill-meta.json
│   │   ├── scripts/          (merge_excel.py, merge_excel_fast.py)
│   │   └── evals/            (evals.json)
│   ├── pdf-extractor/
│   │   ├── SKILL.md
│   │   ├── skill-meta.json
│   │   ├── scripts/          (pdf_extract.py)
│   │   └── evals/            (evals.json)
│   ├── pdf-to-markdown/
│   ├── risk/
│   │   ├── SKILL.md
│   │   ├── skill-meta.json
│   │   └── scripts/          (generate_report.js)
│   └── xiaoai-reply/
│       ├── SKILL.md
│       └── skill-meta.json
└── users/                         [3 个用户]
    ├── admin/                     [~40 文件] 有 workspace 内容
    │   ├── .claude.json
    │   ├── .claude/               (sessions, backups, settings, telemetry)
    │   ├── logs/                  (agent_server.log)
    │   └── workspace/
    │       ├── .claude/skills/    (skill-creator, xiaoai-reply)
    │       └── outputs/           (若干生成文件)
    ├── xiangyan/                  [~15 文件] 当前 git 用户
    │   ├── .claude.json           (20 KB)
    │   ├── .claude/settings.json
    │   ├── logs/                  (agent_server.log)
    │   └── workspace/
    │       └── .claude/skills/    (8 个技能 symlink)
    └── yguo/                      [~250 文件] 最活跃用户
        ├── .claude.json
        ├── .claude/               (sessions, backups, telemetry, shell-snapshots)
        ├── logs/                  (agent_server.log)
        └── workspace/
            ├── .claude/skills/    (5 个个人技能)
            ├── outputs/           (42+ 生成文件, 多个 sess_*/)
            └── uploads/           (98+ 上传文件: PDF/JPG/XLSX/DOCX)
```

## 二、存储架构

所有数据分两层存储：**SQLite 存结构化元数据，文件系统存大文件/非结构化内容**。

### SQLite 表（web-agent.db，共 15 张表 + 2 FTS5）

| 表名 | 用途 | 存储量级 |
|------|------|---------|
| `users` | 用户账号 | ~10 行 |
| `sessions` | 会话元数据 | ~100 行 |
| `messages` | 对话消息（最大表） | ~10000+ 行 |
| `tasks` | 子代理任务 | ~100 行 |
| `mcp_servers` | MCP 服务配置 | ~10 行 |
| `skill_feedback` | 技能反馈评分 | ~100 行 |
| `skills` | 技能注册表 | ~20 行 |
| `skill_versions` | 技能版本历史 | ~50 行 |
| `skill_usage` | 技能使用事件 | ~1000 行 |
| `skill_promotion_queue` | 晋升候选队列 | ~10 行 |
| `learned_patterns` | 工具共现模式 | ~100 行 |
| `wiki_pages` | 自动生成的 Wiki | ~50 行 |
| `session_summaries` | 会话摘要 | ~100 行 |
| `audit_log` | 审计日志 | ~1000 行 |
| `uploads` | 上传文件元数据 | ~200 行 |
| `generated_files` | 生成文件元数据 | ~200 行 |
| `wiki_fts` | Wiki 全文搜索索引 | FTS5 虚拟表 |
| `session_summary_fts` | 摘要全文搜索索引 | FTS5 虚拟表 |

### 文件系统 vs SQLite 对照

| 数据域 | SQLite | 文件系统 |
|--------|--------|---------|
| 用户账号 | `users` 表 | — |
| 会话元数据 | `sessions` 表 | `users/{uid}/.claude/sessions/`（Agent SDK 原生） |
| 对话消息 | `messages` 表 | — |
| 技能元数据 | `skills`, `skill_versions`, `skill_usage`, `skill_feedback` | — |
| 技能代码 | — | `shared-skills/`, `users/{uid}/workspace/.claude/skills/` |
| 上传文件 | `uploads` 表（元数据） | `users/{uid}/workspace/uploads/`（文件字节） |
| 生成文件 | `generated_files` 表（元数据） | `users/{uid}/workspace/outputs/`（文件字节） |
| Wiki 页面 | `wiki_pages` + `wiki_fts` | — |
| 模式挖掘 | `learned_patterns` | — |
| 审计日志 | `audit_log` | — |
| MCP 配置 | `mcp_servers` 表 | `data/mcp-registry.json`（回退文件） |
| 日志 | — | `users/{uid}/logs/` |
| Agent SDK 状态 | — | `users/{uid}/.claude/`（session, settings, backups, telemetry） |

**核心结论**：技能系统采用了「元数据在 DB + 代码在文件系统」的双重存储模式，通过 `skills.path` 列连接。上传/生成文件同理。

---

## 三、目录创建代码路径

### 3.1 `container_manager.py:91-100` — 用户容器初始化

容器启动时创建 4 个目录：

| 路径 | 用途 | 代码行 |
|------|------|--------|
| `users/{uid}/workspace/uploads` | 上传目录 | line 94 |
| `users/{uid}/.claude/memory` | Claude 持久记忆 | line 95 |
| `users/{uid}/.cache/uv` | uv 包缓存 | line 96 |
| `users/{uid}/logs` | 容器日志 | line 97 |

### 3.2 `main_server.py:1562` — SDK 选项构建

- `users/{uid}/workspace/outputs` — 每次运行 agent 前确保存在

### 3.3 Session 端点创建

- `users/{uid}/workspace/outputs/{session_id}` — `run_agent_task()` line 3136 + `create_session()` line 3455

### 3.4 上传端点

- `users/{uid}/workspace/uploads/{session_id}` / `users/{uid}/workspace/uploads` — `upload_file()` line 3796

### 3.5 技能管理端点

| 路径 | 触发点 | 代码行 |
|------|--------|--------|
| `users/{uid}/workspace/.claude/skills/{name}` | 个人技能上传 | line 4292 |
| `shared-skills/{name}` | 共享技能上传 | line 4318 |
| `shared-skills/{name}` | 技能晋升（personal → shared） | line 4569 |
| `shared-skills/{name}/versions/v{N}` | 进化版本 | line 5001 |
| `shared-skills/{name}@v{N}` | 技能版本（平坦命名） | `skill_feedback.py:585` |

### 3.6 日志系统

| 路径 | 默认值 | 代码位置 |
|------|--------|---------|
| `APP_LOG_DIR` | `/data/logs/app` | `app_logger.py:25,56` |
| `AGENT_LOG_DIR/{uid}` | `/data/logs/agent/{uid}` | `agent_logger.py:25,46` |

### 3.7 数据库初始化

- `DATA_ROOT/` — `database.py:258`（确保 web-agent.db 父目录存在）
- `DATA_ROOT/` — `main_server.py:5877`（保存 MCP registry 时确保存在）

### 3.8 容器内 Hook

- `/workspace/.audit/` — `hooks/post_tool_use.py:25`（映射为 `users/{uid}/workspace/.audit`）

---

## 四、发现的问题

### 4.1 损坏的符号链接 🔴

`shared-skills/test-skill` → `data/skills/test-skill`（目标不存在）

**状态**：✅ 已删除（2026-05-19 第二轮）

### 4.2 旧 memory 路径残留 🔴

`data/users/yguo/memory/` 包含 `111` 和 `user.md`。`src/memory.py` 已删除。

**状态**：✅ 已删除（2026-05-19 第二轮）

### 4.3 .DS_Store 污染 🔴

4 个 macOS 元数据文件分散在 `data/` 中。

**状态**：✅ 已删除（2026-05-19 第二轮）

### 4.4 日志路径不一致 🟡

`app_logger.py` 和 `agent_logger.py` 默认路径为绝对路径 `/data/logs/`（不以 `DATA_ROOT` 为前缀）。本地开发 `DATA_ROOT=./data` 时，日志写入系统根目录 `/data/logs/`。

**状态**：⏳ 待修复 — 需在 `.env` 中显式设置 `APP_LOG_DIR` 和 `AGENT_LOG_DIR`

### 4.5 WAL 文件偏大 🟡

原 `web-agent.db-wal` = 15.5 MB。

**状态**：✅ 已修复 — `PRAGMA wal_checkpoint(TRUNCATE)` 后缩至 0 字节

### 4.6 用户目录残留 sessions.db 🟡

`data/users/yguo/sessions.db` — 经检查为 0 字节空文件。

**状态**：✅ 已删除（2026-05-19 第二轮）

### 4.7 容器缓存污染用户目录 🟡

`data/users/admin/Library/Caches/`（3.8 MB）：MCP 日志 + pip 缓存。

**状态**：✅ 已删除（2026-05-19 第二轮）。长期修复需在 `container_manager.py` 中将容器内 `HOME/Library/Caches` 重定向到非持久化路径。

### 4.8 空壳用户 🟢

containertest、lisi、testuser、zhangsan 4 个用户不在 `users` 表中有记录。

**状态**：✅ 已删除（2026-05-19 第二轮）。xiangyan 为当前 git 用户，保留。

---

## 五、数据清理记录

### 2026-05-19 第一轮清理

| 操作 | 数量 |
|------|------|
| 删除空 `data/logs/` | 1 个目录 |
| 删除废弃测试用户目录 | 15 个（ctest, ctest2, e2etest, final-test, fintest, fresh-test, stream-test, test_user, testuser2, think-test2, threadtest, tool-test, tool-test2, tool-test3, ws-test2） |
| 清理 `cleanup_stale_files.py` 中的 `.msg-buffer` 引用 | 1 处代码修改 |

### 2026-05-19 第二轮清理

| 操作 | 详情 |
|------|------|
| 删除损坏符号链接 | `shared-skills/test-skill` → 不存在的 `data/skills/test-skill` |
| 删除旧 memory 残留 | `yguo/memory/`（111, user.md） |
| 删除 .DS_Store 文件 | 4 处 |
| 删除空 sessions.db | `yguo/sessions.db`（0 字节） |
| 删除容器缓存污染 | `admin/Library/Caches/`（3.8 MB） |
| 删除空壳用户 | containertest, lisi, testuser, zhangsan（DB 中无记录） |
| WAL checkpoint | `web-agent.db-wal` 从 15.5 MB → 0 字节 |

### 清理后最终状态

```
data/
├── web-agent.db                [3.3 MB]
├── web-agent.db-shm            [32 KB]
├── web-agent.db-wal            [0 B]
├── shared-skills/              [8 技能, 324 KB]
│   ├── bank-confirmation-extractor/
│   ├── counterpart-analysis/
│   ├── depreciation-amortization-reconciliation/
│   ├── excel-merger/
│   ├── pdf-extractor/
│   ├── pdf-to-markdown/
│   ├── risk/
│   └── xiaoai-reply/
└── users/                      [3 用户]
    ├── admin/                  [532 KB]
    ├── xiangyan/               [24 KB]
    └── yguo/                   [261 MB]
```

### 待处理

| 项目 | 说明 |
|------|------|
| 日志路径修复 | 在 `.env` 中显式设置 `APP_LOG_DIR` 和 `AGENT_LOG_DIR` 为 DATA_ROOT 相对路径 |
| 容器缓存长期修复 | `container_manager.py` 中将 `HOME/Library/Caches` 重定向到非持久化路径 |

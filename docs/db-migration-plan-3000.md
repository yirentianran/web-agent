# 数据库迁移计划 — 3000 用户

**日期**: 2026-04-16
**范围**: 从文件存储迁移到 PostgreSQL，3000 活跃用户
**状态**: 生产系统 — 需要零停机迁移

---

## 1. 架构决策

### 推荐：PostgreSQL

**理由**：3000 用户是之前分析中 SQLite 触发阈值（50 用户）的 60 倍。

### 容量计算

| 指标 | 当前估算 | SQLite 极限 | PostgreSQL |
|------|---------|------------|------------|
| 并发会话（10% 活跃） | ~300 | 并发写入瓶颈 | 数千连接 |
| 每会话消息写入/轮 | ~50-200 | 单写入者瓶颈 | 连接池，并行写入 |
| list_sessions() I/O | O(N) 每用户 | 仍是 O(N) | B-tree 索引 O(log N) |
| 内存更新（并发） | 读-改-写竞态 | 文件锁竞争 | ACID 事务 |
| 会话历史检索 | 全 JSONL 解析 | 无索引仍全表扫描 | 索引 + 分页查询 |
| 多进程支持 | 仅单进程 | WAL 允许并发读 | 完整多进程/线程 |

### 为什么不用 SQLite

SQLite WAL 模式处理 ~100K 读/秒，但**同时只允许一个写入者**。300 个并发活跃会话每轮写入多条消息，写入竞争成为瓶颈。SQLite 也没有连接池。

### 什么保持文件

| 数据类型 | 存储 | 原因 |
|---------|------|------|
| 审计日志 | JSONL 文件 (`data/logs/audit/`) | 追加写入 + 哈希链 — 防篡改正确模式 |
| 上传/输出 | 文件系统 (`data/users/{user}/workspace/`) | 二进制内容 — 文件是正确介质 |
| 技能 Markdown | 文件系统 | SDK 直接加载 |
| Agent 笔记 (L2) | 文件系统 (`data/users/{user}/memory/*.md`) | 自由文本，自动加载到系统提示 |
| 应用日志 | JSONL 文件 (`data/logs/app/`) | 结构化日志 — 正确模式 |
| 训练数据 (QA) | JSONL 文件 (`data/training/qa/`) | 批量分析，查询频率低 |

### 什么迁移到 PostgreSQL

| 数据类型 | 当前 | 新表 |
|---------|------|------|
| 会话消息 | `data/.msg-buffer/{id}.jsonl` | `messages` |
| 会话元数据 | `claude-data/sessions/` | `sessions` |
| 用户内存 (L1) | `users/{user}/memory.json` | `user_memory` |
| 子代理任务 | `users/{user}/tasks/{id}.json` | `tasks` |
| 技能反馈 | `training/skill-feedback/*.jsonl` | `skill_feedback` |
| MCP 注册 | `mcp-registry.json` | `mcp_servers` |
| A/B 测试结果 | `training/skill_outcomes/*.jsonl` | `ab_test_results` |

---

## 2. 数据库 Schema 设计

### 2.1 核心表

```sql
-- 用户表（新建 — 用于外键）
CREATE TABLE users (
    id VARCHAR(64) PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    preferences JSONB NOT NULL DEFAULT '{}'
);

-- 会话表
CREATE TABLE sessions (
    id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL DEFAULT 'idle',
    -- idle | running | completed | error | waiting_user | cancelled
    cost_usd NUMERIC(10, 4) NOT NULL DEFAULT 0,
    message_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    forked_from VARCHAR(128) REFERENCES sessions(id)
);

CREATE INDEX idx_sessions_user_created ON sessions(user_id, created_at DESC);
CREATE INDEX idx_sessions_user_status ON sessions(user_id, status);
CREATE INDEX idx_sessions_last_active ON sessions(last_active_at);

-- 消息表
CREATE TABLE messages (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(128) NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,  -- 每会话序列号，用于排序
    type VARCHAR(30) NOT NULL,
    -- user | assistant | system | tool_use | tool_result | error | heartbeat | file_result
    subtype VARCHAR(50),
    name VARCHAR(100),  -- tool_use/tool_result 的工具名
    content TEXT,
    payload JSONB,  -- 复杂类型的完整消息字典
    usage JSONB,  -- token 使用统计
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_messages_session_seq ON messages(session_id, seq);
CREATE INDEX idx_messages_session ON messages(session_id);

-- 用户内存（L1 平台内存）
CREATE TABLE user_memory (
    user_id VARCHAR(64) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    preferences JSONB NOT NULL DEFAULT '{}',
    entity_memory JSONB NOT NULL DEFAULT '{}',
    audit_context JSONB NOT NULL DEFAULT '{}',
    file_memory JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 任务（子代理）
CREATE TABLE tasks (
    id VARCHAR(32) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    active_form TEXT NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    blocked_by TEXT[] NOT NULL DEFAULT '{}',
    parent_task_id VARCHAR(32) REFERENCES tasks(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_tasks_user_status ON tasks(user_id, status);
CREATE INDEX idx_tasks_user_created ON tasks(user_id, created_at DESC);
CREATE INDEX idx_tasks_parent ON tasks(parent_task_id);

-- 技能反馈
CREATE TABLE skill_feedback (
    id BIGSERIAL PRIMARY KEY,
    skill_name VARCHAR(100) NOT NULL,
    user_id VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id VARCHAR(128),
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment TEXT NOT NULL DEFAULT '',
    skill_version VARCHAR(50) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_skill_feedback_skill ON skill_feedback(skill_name);
CREATE INDEX idx_skill_feedback_user ON skill_feedback(user_id);
CREATE INDEX idx_skill_feedback_created ON skill_feedback(created_at);

-- MCP 服务器（全局配置）
CREATE TABLE mcp_servers (
    name VARCHAR(100) PRIMARY KEY,
    server_type VARCHAR(20) NOT NULL DEFAULT 'stdio',
    command TEXT,
    args JSONB,
    url TEXT,
    env JSONB,
    enabled_tools TEXT[] NOT NULL DEFAULT '{}',
    description TEXT NOT NULL DEFAULT '',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    access VARCHAR(10) NOT NULL DEFAULT 'all',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- A/B 测试结果
CREATE TABLE ab_test_results (
    id BIGSERIAL PRIMARY KEY,
    skill_name VARCHAR(100) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    version VARCHAR(1) NOT NULL CHECK (version IN ('a', 'b')),
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ab_test_skill ON ab_test_results(skill_name);
CREATE INDEX idx_ab_test_skill_version ON ab_test_results(skill_name, version);
```

### 2.2 设计决策

1. **消息使用 `seq`（序列号）而非依赖 `id`** — 支持干净的分页 `WHERE seq > :after_index`
2. **`payload` JSONB 列** — 存储复杂类型的完整消息字典，需要时可查询内部
3. **`content` TEXT 列** — 简单文本消息，避免常见情况的 JSONB 开销
4. **外键 CASCADE** — 删除用户级联删除其所有数据
5. **`users` 表** — 新建，跟踪用户生命周期。首次会话或认证 token 时创建

---

## 3. 迁移策略

### 3.1 方案：双写 + 功能标志

3000 在线用户不能承受长时间停机。迁移使用**双写、渐进式切换**方案：

```
阶段 A：DB 就绪，从文件读取 (DB_WRITE=false)
阶段 B：双写到 DB + 文件 (DB_WRITE=true, DB_READ=false)
阶段 C：从 DB 读取，双写 (DB_READ=true, DB_WRITE=true)
阶段 D：仅 DB (DB_READ=true, DB_WRITE=true, FILE_FALLBACK=true)
阶段 E：清理文件 (仅 DB，文件归档)
```

### 3.2 数据映射

#### 会话消息（JSONL → messages 表）

```
data/.msg-buffer/{session_id}.jsonl 每行映射为:
  - session_id: 从文件名
  - seq: 行号（0 起始）
  - type: message["type"]
  - subtype: message.get("subtype")
  - name: message.get("name")
  - content: message.get("content", "")
  - payload: 完整消息字典（JSONB）用于复杂类型
  - usage: message.get("usage")
```

#### 会话元数据（JSONL + meta.json → sessions 表）

```
从 .jsonl 首行:
  - created_at: data["timestamp"]
  - status: 从首条消息类型推导
从 .meta.json（如存在）:
  - title: meta["title"]
从消息缓冲:
  - message_count: JSONL 行数
  - cost_usd: 缓冲状态
  - last_active_at: 文件修改时间
```

#### 用户内存（memory.json → user_memory 表）

```
每个 data/users/{user_id}/memory.json 映射为:
  - user_id: 从路径
  - preferences: data.get("preferences", {})
  - entity_memory: data.get("entity_memory", {})
  - audit_context: data.get("audit_context", {})
  - file_memory: data.get("file_memory", [])
```

### 3.3 迁移脚本

文件：`scripts/migrate_files_to_db.py`

```
1. 连接 PostgreSQL
2. 扫描 data/users/ 所有用户目录
3. 对每个用户:
   a. 插入 users 表（id, created_at）
   b. 如 memory.json 存在 → 插入 user_memory
   c. 对 .msg-buffer/ 中每个 .jsonl:
      - 从文件名解析 session_id
      - 插入 sessions 表
      - 批量插入每行到 messages 表（每批 1000 条）
   d. 对 tasks/ 中每个 .json:
      - 插入 tasks 表
4. 扫描 training/skill-feedback/ → 插入 skill_feedback
5. 解析 mcp-registry.json → 插入 mcp_servers
6. 扫描 training/skill_outcomes/ → 插入 ab_test_results
7. 验证计数匹配
```

**性能优化**：使用 `asyncpg` 批量插入 `executemany()`。3000 用户 × 50 会话 × 200 消息 = 3000 万消息。10K 插入/秒 = ~50 分钟。

### 3.4 零停机部署

```
步骤 1: 部署 PostgreSQL  alongside 现有文件存储
步骤 2: 运行迁移脚本（读文件，写 DB）— ~1 小时
步骤 3: 部署代码启用双写（同时写两边）
步骤 4: 监控 24-48 小时，验证数据一致性
步骤 5: 切换读取到 DB（功能标志）
步骤 6: 监控 48 小时
步骤 7: 移除双写，归档文件数据
```

**停机时间**: 无。迁移脚本读取文件时服务器继续运行。双写确保过渡期不丢数据。

### 3.5 回滚方案

```
步骤 5（读取切换）后出现问题:
1. 设置 DB_READ=false → 读取回退到文件
2. 文件仍在双写中
3. 无数据丢失，无停机

步骤 7（文件删除）后出现问题:
1. 从文件备份恢复（步骤 7 前创建）
2. 在备份上重新运行迁移脚本
3. 设置 DB_READ=false
```

---

## 4. 实施阶段

### 阶段 1：数据库基础设施（1-2 天）

**目标**：PostgreSQL 实例运行，schema 应用，连接测试通过。

| 步骤 | 文件 | 行动 |
|------|------|------|
| 1 | `pyproject.toml` | 添加 `asyncpg>=0.29.0` 依赖 |
| 2 | `src/database.py` | **新建** — 数据库连接池，启动/关闭生命周期 |
| 3 | `src/db_models.py` | **新建** — 匹配 DB schema 的 Pydantic 模型 |
| 4 | `scripts/init_db.sql` | **新建** — 完整 SQL schema |
| 5 | `scripts/migrate_files_to_db.py` | **新建** — 迁移脚本 |
| 6 | `main_server.py` | 在 lifespan 事件中初始化 DB 池 |

### 阶段 2：会话存储迁移（3-4 天）

**目标**：会话和消息从 DB 读写（功能标志控制）。

| 步骤 | 文件 | 行动 | 依赖 |
|------|------|------|------|
| 1 | `src/session_store.py` | **新建** — DB 会话存储 + 文件回退 | 阶段 1 |
| 2 | `main_server.py` | 会话端点改用 SessionStore | 步骤 1 |
| 3 | `src/message_buffer.py` | 添加 DB 写路径（双写） | 步骤 1 |
| 4 | `src/feature_flags.py` | **新建** — 功能标志系统 | 无 |
| 5 | `tests/unit/test_session_store.py` | **新建** — SessionStore 单元测试 | 步骤 1 |
| 6 | `tests/integration/test_session_migration.py` | **新建** — 验证文件/DB 数据一致 | 步骤 2 |

### 阶段 3：用户内存迁移（2 天）

**目标**：用户内存使用 DB 事务（消除读-改-写竞态）。

| 步骤 | 文件 | 行动 | 依赖 |
|------|------|------|------|
| 1 | `src/memory.py` | 添加 DB 读/写路径 + 事务 | 阶段 2 |
| 2 | `main_server.py` | 内存端点使用 MemoryManager（已连接） | 步骤 1 |
| 3 | `tests/unit/test_memory_db.py` | **新建** — 测试并发内存更新 | 步骤 1 |

### 阶段 4：任务、反馈、MCP 迁移（2 天）

**目标**：所有剩余结构化数据入 DB。

| 步骤 | 文件 | 行动 | 依赖 |
|------|------|------|------|
| 1 | `src/sub_agent.py` | 添加任务 CRUD 的 DB 路径 | 阶段 2 |
| 2 | `src/skill_feedback.py` | 添加反馈提交 + 分析的 DB 路径 | 阶段 2 |
| 3 | `main_server.py` | MCP 端点使用 DB | 阶段 2 |
| 4 | `src/ab_testing.py` | 添加 A/B 测试记录的 DB 路径 | 阶段 2 |

### 阶段 5：迁移脚本 + 数据验证（2 天）

**目标**：运行迁移脚本，验证数据完整性。

| 步骤 | 文件 | 行动 | 依赖 |
|------|------|------|------|
| 1 | `scripts/migrate_files_to_db.py` | 完成迁移脚本 | 阶段 1-4 |
| 2 | `scripts/verify_migration.py` | **新建** — 对比文件 vs DB 数据 | 步骤 1 |
| 3 | 先在预演环境运行迁移 | — | 步骤 2 |
| 4 | 在生产环境运行迁移 | — | 步骤 3 |

### 阶段 6：功能标志切换（1 天）

**目标**：渐进式切换到 DB 读取。

| 步骤 | 行动 | 持续时间 |
|------|------|---------|
| 1 | 部署启用 DB_WRITE=true（双写） | 24h 监控 |
| 2 | 部署启用 DB_READ=true（从 DB 读取） | 48h 监控 |
| 3 | 部署启用 FILE_FALLBACK=false（仅 DB） | 48h 监控 |

### 阶段 7：文件清理（1 天）

**目标**：归档并删除已迁移的文件数据。

| 步骤 | 行动 |
|------|------|
| 1 | 备份所有文件数据到 `data/archive-pre-migration/` |
| 2 | 删除 `.msg-buffer/` JSONL 文件 |
| 3 | 删除 `claude-data/sessions/` 文件 |
| 4 | 删除 `memory.json` 文件 |
| 5 | 删除 `tasks/` JSON 文件 |
| 6 | 删除 `training/skill-feedback/` JSONL 文件 |
| 7 | 删除 `mcp-registry.json` |
| 8 | 保留：审计日志、上传、输出、技能、Agent 笔记、应用日志 |

---

## 5. API 变更

### 需要变更的端点（内部实现变更，外部 API 不变）

| 端点 | 文件 | 变更 |
|------|------|------|
| `POST /api/users/{user_id}/sessions` | `main_server.py` | SessionStore.create_session() 替代文件 touch |
| `GET /api/users/{user_id}/sessions` | `main_server.py` | SessionStore.list_sessions() — 索引查询替代 glob |
| `GET /api/users/{user_id}/sessions/{id}/history` | `main_server.py` | 查询 messages 表 + 分页 |
| `DELETE /api/users/{user_id}/sessions/{id}` | `main_server.py` | SessionStore.delete_session() — CASCADE 处理清理 |
| `PATCH /api/users/{user_id}/sessions/{id}/title` | `main_server.py` | UPDATE sessions SET title=... |
| `GET /api/users/{user_id}/memory` | `main_server.py` | 查询 user_memory 表 |
| `PUT /api/users/{user_id}/memory` | `main_server.py` | UPSERT user_memory 事务 |
| `POST /api/users/{user_id}/tasks` | `main_server.py` | INSERT INTO tasks |
| `GET /api/users/{user_id}/tasks` | `main_server.py` | SELECT FROM tasks WHERE user_id |
| `POST /api/skills/{name}/feedback` | `main_server.py` | INSERT INTO skill_feedback |
| `GET /api/skills/{name}/analytics` | `main_server.py` | AVG/COUNT 聚合查询 |
| `GET/POST/DELETE /api/admin/mcp-servers` | `main_server.py` | mcp_servers 表 CRUD |

### 不需要变更的端点

| 端点 | 原因 |
|------|------|
| 所有文件上传/下载端点 | 文件保留在文件系统 |
| 所有技能 CRUD 端点 | 技能保持为磁盘目录 |
| 所有 Agent 笔记端点 | Agent 笔记保持 Markdown 文件 |
| WebSocket 端点 | 使用内存 MessageBuffer，DB 仅用于持久化 |
| 认证端点 | JWT，无 DB 参与 |
| 健康检查端点 | 无变更 |

---

## 6. 性能目标

### 查询延迟目标

| 操作 | 当前（文件） | 目标（DB） | p99 目标 |
|------|------------|-----------|---------|
| list_sessions() | O(N) 文件读取, ~200-500ms | 索引查询, <10ms | <50ms |
| get_history() | 全 JSONL 解析, ~50-200ms | 索引分页查询, <20ms | <100ms |
| create_session() | 文件 touch, <5ms | INSERT, <5ms | <20ms |
| update_memory() | 读-改-写, ~10ms | UPSERT 事务, <5ms | <20ms |
| submit_feedback() | 文件写入, ~5ms | INSERT, <5ms | <20ms |
| get_skill_analytics() | glob + 解析所有文件, ~100ms | SQL 聚合, <20ms | <50ms |

### 连接池配置

```
min_size: 5   （基线流量的预热连接）
max_size: 20  （处理突发并发请求）

理由:
  - 3000 用户, ~10% 并发 = 300 活跃会话
  - 但并非所有会话同时命中 DB
  - WebSocket 使用内存缓冲，仅持久化时写 DB
  - REST 端点是请求级别的，非长连接
  - max_size=20 处理 ~20 并发 DB 操作
  - 如监控显示池耗尽，增加到 40
```

---

## 7. 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 迁移脚本损坏数据 | 低 | 高 | 先在预演环境运行；验证计数；生产运行前备份文件 |
| DB 连接池在高负载下耗尽 | 中 | 高 | 监控池指标；max_size=20 起始，可扩展到 40+ |
| 功能标志回滚失败 | 低 | 中 | 功能标志是环境变量 — 通过配置变更即时回滚 |
| 迁移后消息序列断裂 | 低 | 中 | 迁移脚本验证 seq 连续性 |
| PostgreSQL 单点故障 | 中 | 高 | 使用托管服务（RDS/Supabase）自动故障转移；定期备份 |
| 内存迁移丢失并发更新 | 低 | 高 | 双写阶段捕获 — 切换读取前验证文件和 DB 匹配 |
| 大消息（>1MB）拖慢查询 | 中 | 中 | `src/truncation.py` 已截断工具结果为 200 字符 |
| 迁移完成前运行会话清理 | 低 | 高 | 迁移期间禁用清理（功能标志） |
| 回滚需要恢复文件数据 | 低 | 高 | 迁移后保留文件备份 30 天；记录恢复流程 |

---

## 8. 时间线

### 第 1 周：基础

| 日期 | 活动 | 交付物 |
|------|------|--------|
| 周一-周二 | 阶段 1 — DB 基础设施 | PostgreSQL 运行，schema 应用，连接池 |
| 周三-周四 | 阶段 2（部分）— SessionStore | 带文件回退的 SessionStore，功能标志 |
| 周五 | 阶段 2（部分）— 连接端点 | list_sessions/create_session 使用 SessionStore |

### 第 2 周：会话 + 内存迁移

| 日期 | 活动 | 交付物 |
|------|------|--------|
| 周一-周二 | 阶段 2（完成）— MessageBuffer DB 路径 | 双写消息到 DB + 磁盘 |
| 周三 | 阶段 3 — 内存 DB 路径 | 带 DB 事务的 MemoryManager |
| 周四-周五 | 阶段 4 — 任务、反馈、MCP | 所有结构化数据有 DB 路径 |

### 第 3 周：迁移 + 验证

| 日期 | 活动 | 交付物 |
|------|------|--------|
| 周一-周三 | 阶段 5 — 迁移脚本 | 在预演环境测试，数据验证通过 |
| 周四 | 阶段 5 — 生产迁移 | 在实时数据上运行迁移 |
| 周五 | 阶段 6 — 功能标志切换（双写） | DB_WRITE=true，监控 24h |

### 第 4 周：切换 + 清理

| 日期 | 活动 | 交付物 |
|------|------|--------|
| 周一-周二 | 阶段 6 — 读取切换 | DB_READ=true，监控 48h |
| 周三 | 阶段 6 — 仅 DB | FILE_FALLBACK=false |
| 周四-周五 | 阶段 7 — 文件清理 | 归档并删除已迁移文件 |

### 可并行工作

- 阶段 1（DB 基础设施）和阶段 4 准备（任务/反馈模型变更）可并行
- 测试（阶段 2 测试）可在 SessionStore 编写后立即开始
- 迁移脚本可与阶段 2-4 实现并行开发

### 不可并行工作

- 阶段 5（迁移脚本）依赖所有 DB 模型稳定（阶段 1-4）
- 阶段 6（功能标志切换）必须顺序：写 → 读 → 仅
- 阶段 7（清理）必须在阶段 6 成功完成后进行

---

## 9. 测试策略

### 单元测试

| 文件 | 测试 |
|------|------|
| `tests/unit/test_session_store.py` | CRUD 操作、分页、错误处理 |
| `tests/unit/test_memory_db.py` | 并发更新、事务隔离 |
| `tests/unit/test_database.py` | 连接池生命周期、查询错误 |
| `tests/unit/test_feature_flags.py` | 标志组合、回退行为 |

### 集成测试

| 文件 | 测试 |
|------|------|
| `tests/integration/test_session_migration.py` | 对比文件数据 vs DB 数据（示例用户） |
| `tests/integration/test_db_endpoints.py` | 所有变更端点从 DB 和文件返回相同数据 |
| `tests/integration/test_concurrent_memory.py` | 两个"并发"内存更新，验证无丢失更新 |

### 验收标准

- [ ] 所有现有测试通过（无回归）
- [ ] 新单元测试覆盖 SessionStore、MemoryManager DB 路径、database.py
- [ ] 集成测试验证 10 个示例用户的文件/DB 数据一致性
- [ ] 迁移脚本以 100% 准确率处理 10 个测试用户
- [ ] 功能标志回滚有效（DB_READ=false，读取回退到文件）
- [ ] 所有修改端点达到 p99 延迟目标

---

## 10. 关键文件清单

### 新建文件

| 文件 | 用途 |
|------|------|
| `src/database.py` | 连接池 |
| `src/session_store.py` | 抽象会话存储（DB + 文件） |
| `src/feature_flags.py` | 迁移功能标志 |
| `src/db_models.py` | Pydantic DB 模型 |
| `scripts/init_db.sql` | 数据库 schema |
| `scripts/migrate_files_to_db.py` | 迁移脚本 |
| `scripts/verify_migration.py` | 数据验证 |

### 修改文件

| 文件 | 变更 |
|------|------|
| `main_server.py` | 端点改用 DB 支持的存储 |
| `src/message_buffer.py` | 添加 DB 写路径 |
| `src/memory.py` | 添加 DB 读/写 + 事务 |
| `src/sub_agent.py` | 添加任务 CRUD 的 DB 路径 |
| `src/skill_feedback.py` | 添加反馈的 DB 路径 |
| `src/ab_testing.py` | 添加 A/B 测试的 DB 路径 |
| `pyproject.toml` | 添加 asyncpg 依赖 |

---

## 11. 总结

**当前状态**：文件存储在 3000 用户下因 O(N) 文件 I/O、无事务支持、无连接池将严重退化。

**推荐**：迁移结构化数据（会话、消息、内存、任务、反馈、MCP 配置）到 PostgreSQL。保留追加写入文件（审计日志、应用日志）和二进制内容（上传、输出、技能）在文件系统。

**方案**：双写 + 功能标志实现零停机迁移。7 个阶段约 4 周，每个阶段独立可测试、可回滚。

**风险等级**：中。通过双写方案、功能标志回滚和预演环境充分测试来缓解。

# SQLite 迁移计划 — 3000 用户（零安装）

**日期**: 2026-04-16
**数据库**: SQLite（Python 内置 `sqlite3` + `aiosqlite` 异步支持）
**状态**: 零安装、零外部依赖

---

## 1. 为什么选 SQLite

| 需求 | SQLite 能力 |
|------|-----------|
| 零安装 | Python 内置 `sqlite3`，只需 `pip install aiosqlite` |
| 3000 用户 | WAL 模式下支持并发读，单写者序列化写入 |
| 查询性能 | B-tree 索引，O(log N) 替代 O(N) 文件扫描 |
| 事务 | ACID 事务，解决 memory.json 并发丢失更新 |
| 分页 | 原生 LIMIT/OFFSET |
| 运维成本 | 零 — 就是一个文件 |

### 容量评估

SQLite 理论极限 280TB，实际建议：
- **写入吞吐**: WAL 模式下 ~10K 写入/秒
- **并发读**: 无限制
- **并发写**: 串行化（一次一个）

3000 用户的写入模式：
- 不是所有用户同时发消息
- 消息写入是追加（快）
- 列表/查询是读操作（WAL 无竞争）

**结论**: SQLite 够用。如果后续写入成为瓶颈（>100 并发写），再迁移 PostgreSQL。

---

## 2. Schema 设计

```sql
-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    last_active_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- 会话表
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'idle',
    cost_usd REAL NOT NULL DEFAULT 0,
    message_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    last_active_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_created ON sessions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_user_status ON sessions(user_id, status);

-- 消息表
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    subtype TEXT,
    name TEXT,
    content TEXT,
    payload TEXT,
    usage TEXT,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_session_seq ON messages(session_id, seq);

-- 用户内存表
CREATE TABLE IF NOT EXISTS user_memory (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    preferences TEXT NOT NULL DEFAULT '{}',
    entity_memory TEXT NOT NULL DEFAULT '{}',
    audit_context TEXT NOT NULL DEFAULT '{}',
    file_memory TEXT NOT NULL DEFAULT '[]',
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- 任务表
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    subject TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    active_form TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    blocked_by TEXT NOT NULL DEFAULT '[]',
    parent_task_id TEXT REFERENCES tasks(id),
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_created ON tasks(user_id, created_at DESC);

-- 技能反馈表
CREATE TABLE IF NOT EXISTS skill_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id),
    session_id TEXT,
    rating INTEGER NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    skill_version TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_skill_feedback_skill ON skill_feedback(skill_name);
```

### 设计决策

- **时间戳用 REAL（Unix timestamp）** — 与现有代码的 `time.time()` 一致，无需转换
- **JSON 存 TEXT** — 读写时 `json.dumps/loads`，SQLite JSON1 扩展可选
- **CASCADE 不启用** — SQLite 外键默认关闭，需要 `PRAGMA foreign_keys = ON`，这里手动管理更可控

---

## 3. 实施阶段

### 阶段 1：基础设施（0.5 天）

**新建文件**:
- `src/database.py` — SQLite 连接管理 + `aiosqlite` 异步包装
- `scripts/init_db.py` — 建表脚本

**`src/database.py`** 核心接口：
```python
import aiosqlite
from pathlib import Path

_db_path: Path | None = None
_initialized = False

async def get_db() -> aiosqlite.Connection:
    """Get a connection for the current request."""
    db = await aiosqlite.connect(str(_db_path))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA busy_timeout=5000")
    return db
```

**修改**:
- `pyproject.toml` — 添加 `aiosqlite>=0.20.0`

### 阶段 2：会话 + 消息迁移（1-2 天）

**新建文件**:
- `src/session_store.py` — 统一会话存储（DB 优先，文件回退）

**核心改动**:

| 函数 | 旧实现 | 新实现 |
|------|--------|--------|
| `create_session()` | touch 空 JSONL | INSERT INTO sessions + users |
| `list_sessions()` | glob + 逐文件读取 | SELECT FROM sessions WHERE user_id ORDER BY created_at DESC |
| `get_session_history()` | buffer.get_history() + 磁盘回退 | SELECT FROM messages WHERE session_id ORDER BY seq |
| `delete_session()` | unlink 文件 + remove_session | DELETE FROM sessions + messages（文件也删） |
| `update_session_title()` | 写 .meta.json | UPDATE sessions SET title=... |

**`MessageBuffer.add_message()`** 添加双写：
```python
def add_message(self, session_id, message):
    # ... 现有内存逻辑不变 ...
    self._write_disk(session_id, message)  # 保留文件写入
    if DB_ENABLED:
        asyncio.create_task(self._write_db(session_id, message))  # 异步写 DB
```

**迁移脚本** `scripts/migrate_to_sqlite.py`:
1. 扫描 `data/.msg-buffer/*.jsonl` → INSERT sessions + messages
2. 扫描 `data/users/*/memory.json` → INSERT user_memory
3. 扫描 `data/users/*/tasks/*.json` → INSERT tasks
4. 验证计数匹配

### 阶段 3：用户内存迁移（0.5 天）

**修改** `src/memory.py`:

```python
async def read(self) -> dict:
    if DB_ENABLED:
        row = await db.fetchone("SELECT * FROM user_memory WHERE user_id=?", (self.user_id,))
        if row:
            return dict(row)
    # 文件回退
    return self._read_file()

async def update(self, patch: dict) -> dict:
    if DB_ENABLED:
        # 先读取，深合并，再写入 — 但在事务中
        async with db.transaction():
            current = await self.read()
            updated = _deep_merge(current, patch)
            await db.execute("REPLACE INTO user_memory ...", (...))
            return updated
    # 文件回退
    return self._update_file(patch)
```

### 阶段 4：任务 + 反馈迁移（0.5 天）

**修改**:
- `src/sub_agent.py` — 任务 CRUD 改用 DB
- `src/skill_feedback.py` — 反馈提交 + 分析改用 DB

### 阶段 5：验证 + 切换（0.5 天）

1. 运行迁移脚本
2. 对比文件/DB 数据
3. 启用 `DB_ENABLED = True`
4. 保留文件作为备份 30 天

---

## 4. 什么保持文件

| 数据 | 原因 |
|------|------|
| 审计日志 | 哈希链防篡改 |
| 上传/输出 | 二进制内容 |
| 技能文件 | SDK 直接加载目录 |
| Agent 笔记 | Markdown 文件加载到系统提示 |

---

## 5. 文件清单

### 新建（4 个）
| 文件 | 用途 |
|------|------|
| `src/database.py` | SQLite 连接 + 初始化 |
| `src/session_store.py` | 统一会话存储 |
| `scripts/init_db.py` | 建表 |
| `scripts/migrate_to_sqlite.py` | 数据迁移 |

### 修改（5 个）
| 文件 | 变更 |
|------|------|
| `main_server.py` | 端点改用 SessionStore |
| `src/message_buffer.py` | add_message 双写 DB |
| `src/memory.py` | read/update 改用 DB + 事务 |
| `src/sub_agent.py` | 任务 CRUD 改用 DB |
| `pyproject.toml` | 添加 aiosqlite |

### 保留不变
| 文件 | 原因 |
|------|------|
| `src/audit_logger.py` | 审计日志保持文件 |
| `src/skill_feedback.py` | 迁移到 DB（阶段 4） |
| 所有文件上传/下载端点 | 二进制保持文件系统 |

---

## 6. 时间线

| 阶段 | 时间 | 交付 |
|------|------|------|
| 1. 基础设施 | 0.5 天 | DB 连接 + 建表 |
| 2. 会话+消息 | 1-2 天 | SessionStore + 双写 |
| 3. 用户内存 | 0.5 天 | 事务化内存更新 |
| 4. 任务+反馈 | 0.5 天 | 全部结构化数据入 DB |
| 5. 验证+切换 | 0.5 天 | 迁移完成 |

**总计**: 3-5 天，5 个文件新建，5 个文件修改

---

## 7. 性能目标

| 操作 | 当前 | 目标 |
|------|------|------|
| list_sessions() | O(N) 文件扫描 | <10ms |
| get_history() | 全 JSONL 解析 | <20ms |
| update_memory() | 无锁读-改-写 | 事务，无竞态 |

---

## 8. 回滚方案

SQLite 就是一个文件 `data/web-agent.db`：
1. 迁移前备份 `cp data/web-agent.db data/web-agent.db.bak`
2. 出问题 → 恢复备份
3. 文件数据保留 30 天作为二次备份

零风险，随时可回退。

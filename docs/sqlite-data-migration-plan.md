# SQLite 数据迁移整改方案

> 生成日期: 2026-04-20
> 状态: 待审批
> 基于: `docs/` 下的数据库分析报告

## 1. 现状概览

SQLite 数据库已启用（`data/web-agent.db`, ~4.6 MB），但数据迁移不完整。

### 已迁移（正常运行）

| 表 | 行数 | 状态 |
|---|---|---|
| `users` | 14 | 正常 |
| `sessions` | 24 | 正常 |
| `messages` | 849 | 正常 |
| `skill_feedback` | 47 | 正常，JSONL 文件已清空 |

### 表存在但未使用

| 表 | 行数 | 问题 |
|---|---|---|
| `user_memory` | 0 | 代码支持 DB 读写，但 API 端点仍走文件 |
| `tasks` | 0 | `SubAgentManager` 完全文件操作，未对接 DB |

### 完全未迁移

| 数据 | 存储方式 | 问题 |
|---|---|---|
| **MCP 服务器配置** | `data/mcp-registry.json` | 无 DB 表，所有 CRUD 走文件 |
| **A/B 测试** | `data/training/skill_outcomes/*.jsonl` | 无 DB 表，可能暂未启用 |

### 冗余文件（可清理）

| 数据 | 问题 |
|---|---|
| `.msg-buffer/*.jsonl` (40 文件) | 已双写，磁盘文件是多余副本 |
| `users/*/claude-data/sessions/*.jsonl` | 同上 |
| `users/*/claude-data/sessions/*.meta.json` | 旧会话标题残留 |

### 设计上保持文件存储（不动）

| 数据 | 原因 |
|---|---|
| 审计日志 | 哈希链防篡改 |
| 上传/输出文件 | 二进制内容 |
| 技能定义 | SDK 直接从磁盘加载 |
| Agent 笔记 (L2) | Markdown 注入系统提示 |
| 工作区文件 | 用户生成的代码 |

---

## 2. 整改目标

1. **MCP 服务器配置迁移到 SQLite** — 新增 `mcp_servers` 表，替换所有文件读写
2. **Sub-agent 任务对接 SQLite** — `tasks` 表已存在，需改写 `SubAgentManager` 和 API 端点
3. **用户内存 API 切换到 DB** — `user_memory` 表已存在，需改写 `/api/users/{user_id}/memory` 端点
4. **清理冗余 JSONL 文件** — 关闭双写，删除已迁移的冗余文件
5. **A/B 测试** — 评估是否启用，暂不迁移

---

## 3. 实施方案

### Phase 1: MCP 服务器配置迁移到 SQLite

**复杂度: 中 | 风险: 低**

#### 3.1 新增数据库表

在 `src/database.py` 的 `_CREATE_TABLES` 中添加：

```sql
CREATE TABLE IF NOT EXISTS mcp_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL DEFAULT 'stdio',
    command TEXT,
    args TEXT NOT NULL DEFAULT '[]',
    url TEXT,
    env TEXT NOT NULL DEFAULT '{}',
    tools TEXT NOT NULL DEFAULT '[]',
    description TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    access TEXT NOT NULL DEFAULT 'all',
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);
```

字段说明：
- `args`, `env`, `tools` 存储为 JSON 字符串（读取时 `json.loads`）
- `enabled` 用 INTEGER 存储布尔值

#### 3.2 创建 MCP 数据访问层

新建 `src/mcp_store.py`，提供 async CRUD 方法：

```python
class MCPServerStore:
    def __init__(self, db: Database): ...
    async def list_all(self) -> list[dict]: ...
    async def create(self, server: dict) -> dict: ...
    async def update(self, name: str, server: dict) -> dict | None: ...
    async def delete(self, name: str) -> bool: ...
    async def toggle(self, name: str, enabled: bool) -> bool: ...
    async def get_by_name(self, name: str) -> dict | None: ...
```

#### 3.3 迁移脚本

新建 `scripts/migrate_mcp_to_sqlite.py`：
1. 读取 `data/mcp-registry.json`
2. INSERT 到 `mcp_servers` 表
3. 保留原文件作为备份（添加 `.bak` 后缀）

#### 3.4 替换 API 端点

修改 `main_server.py` 中的 MCP 端点（3052-3126 行）：
- 将 `load_mcp_config()` / `save_mcp_config()` 替换为 `MCPServerStore` 调用
- 所有端点改为 async
- 保持 API 接口不变（前端无需改动）

#### 3.5 初始化集成

在 `main_server.py` 启动时初始化 `MCPServerStore`，类似已有的 `skill_feedback` 迁移模式。

#### 3.6 `load_mcp_config()` 调用点迁移

`main_server.py` 中还有两处非端点调用：
- `line 453`: 构建系统提示时加载 MCP 工具列表
- `line 2700`: 构建 `allowed_tools` 时

这两处需改为从 `MCPServerStore` 读取。

---

### Phase 2: Sub-agent 任务对接 SQLite

**复杂度: 中 | 风险: 低**

#### 2.1 完善 tasks 表

表已存在，需要补充字段：

```sql
ALTER TABLE tasks ADD COLUMN completed_at REAL;
```

#### 2.2 改写 SubAgentManager

修改 `src/sub_agent.py`，使其接受 `db: Database` 参数：

```python
class SubAgentManager:
    def __init__(self, user_id: str, db: Database | None = None) -> None:
        self.user_id = user_id
        self.db = db
        # 如果 db 为 None，回退到文件模式（向后兼容）
```

所有私有方法改为 async + SQL：
- `_load_task()` → `SELECT * FROM tasks WHERE id = ? AND user_id = ?`
- `_save_task()` → `INSERT OR REPLACE INTO tasks ...`
- `_delete_task()` → `DELETE FROM tasks WHERE id = ?`
- `list_tasks()` → `SELECT * FROM tasks WHERE user_id = ?`

保持公共接口不变（方法签名兼容），但改为 `async def`。

#### 2.3 更新 API 端点

修改 `main_server.py` 2240-2308 行：
- 所有 task 端点改为 `async def`
- 使用带 `db` 参数的 `SubAgentManager`
- 保持响应格式不变

#### 2.4 迁移脚本

新建 `scripts/migrate_tasks_to_sqlite.py`：
1. 扫描所有 `data/users/*/tasks/*.json`
2. INSERT 到 `tasks` 表
3. 保留原文件作为备份

---

### Phase 3: 用户内存 API 切换到 DB

**复杂度: 低 | 风险: 低**

#### 3.1 修改 Memory API 端点

修改 `main_server.py` 2148-2179 行：

```python
@app.get("/api/users/{user_id}/memory")
async def get_memory(user_id: str) -> dict:
    # 使用 src/memory.py 中的 MemoryManager 的 DB 路径
    manager = MemoryManager(user_id=user_id, db=_db)
    return await manager.load()

@app.put("/api/users/{user_id}/memory")
async def update_memory(user_id: str, update: MemoryUpdate) -> dict:
    manager = MemoryManager(user_id=user_id, db=_db)
    await manager.update(update.dict(exclude_none=True))
    return {"status": "ok"}
```

`src/memory.py` 已有 DB 读写逻辑（line 80-146），只需确保 API 端点调用它。

#### 3.2 初始化时迁移现有 memory.json

在启动时检查现有用户的 `memory.json` 文件，如果 DB 中没有对应记录则 INSERT。

---

### Phase 4: 清理冗余文件

**复杂度: 低 | 风险: 极低**

#### 4.1 关闭消息双写

修改 `src/message_buffer.py` 和 `src/session_store.py`：
- 移除或注释掉 `_write_disk()` / `_write_disk_session()` 调用
- 确保所有读取路径走 DB

#### 4.2 清理脚本

新建 `scripts/cleanup_stale_files.py`：
1. 删除 `data/.msg-buffer/*.jsonl`
2. 删除 `data/users/*/claude-data/sessions/*.jsonl`
3. 删除 `data/users/*/claude-data/sessions/*.meta.json`
4. 删除空任务目录 `data/users/*/tasks/`（如果已迁移到 DB）
5. 输出清理报告

#### 4.3 `.gitignore` 更新

确保以下路径在 `.gitignore` 中：
```
data/.msg-buffer/
data/users/*/claude-data/
data/users/*/tasks/
data/users/*/memory.json
```

---

### Phase 5: 测试

**复杂度: 中 | 风险: 低**

#### 5.1 MCP 测试

- 测试 CRUD 端点（创建、读取、更新、删除、切换）
- 验证名称冲突处理
- 验证 admin 权限校验

#### 5.2 Task 测试

- 测试 CRUD 端点
- 验证 `blocked_by` 依赖关系
- 验证 `parent_task_id` 父子关系
- 验证状态过滤查询

#### 5.3 Memory 测试

- 测试 GET / PUT 端点
- 验证 deep merge 行为
- 验证 DB 写入后读取一致

#### 5.4 迁移脚本测试

- 在备份数据上运行所有迁移脚本
- 验证迁移后数据完整性
- 验证回滚能力

---

## 4. 依赖关系

```
Phase 1 (MCP) ──┐
                 ├── Phase 4 (清理)
Phase 2 (Tasks) ─┤
                 └── Phase 5 (测试)
Phase 3 (Memory)─┘
```

Phase 1/2/3 可以并行进行（互不依赖）。
Phase 4 依赖 Phase 1/2/3 完成。
Phase 5 依赖所有 Phase 完成。

---

## 5. 风险与缓解

| 风险 | 严重程度 | 缓解措施 |
|---|---|---|
| 迁移过程中数据丢失 | 高 | 先备份 DB 和文件，迁移脚本保留原文件 |
| MCP 配置格式不兼容 | 中 | 迁移脚本做字段验证和默认值填充 |
| Task API 异步改造遗漏 | 中 | 运行类型检查 + 全面测试覆盖 |
| 双写关闭后数据不同步 | 低 | 先关闭写入，保留读取兼容，观察一段时间再删除文件 |

---

## 6. 文件变更清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `src/database.py` | 修改 | 添加 `mcp_servers` 表定义 |
| `src/mcp_store.py` | **新建** | MCP 数据访问层 |
| `src/sub_agent.py` | 修改 | 改为 async + DB 驱动 |
| `src/memory.py` | 不需改 | 已有 DB 支持 |
| `main_server.py` | 修改 | MCP/Tasks/Memory API 端点切换 |
| `scripts/migrate_mcp_to_sqlite.py` | **新建** | MCP 迁移脚本 |
| `scripts/migrate_tasks_to_sqlite.py` | **新建** | Task 迁移脚本 |
| `scripts/cleanup_stale_files.py` | **新建** | 清理脚本 |
| `tests/unit/test_mcp_store.py` | **新建** | MCP 存储测试 |
| `tests/unit/test_sub_agent.py` | 修改 | Task 测试改为 DB 版本 |
| `tests/unit/test_main_server.py` | 修改 | 补充 API 测试 |

---

## 7. 不在范围内

- **A/B 测试迁移** — 该功能可能未启用，暂不处理
- **审计日志迁移** — 设计上保持文件存储
- **前端改动** — 所有 API 接口保持不变
- **用户工作区/技能文件** — 设计上保持文件存储

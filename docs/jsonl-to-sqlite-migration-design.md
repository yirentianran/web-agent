# JSONL 文件全面替换为 SQLite — 设计文档

## 1. 目标

**移除所有 JSONL 文件读写**，统一使用 SQLite 作为消息和 session 元数据的唯一持久化存储。

### 不在范围

- A/B 测试 JSONL（`data/training/skill_outcomes/*.jsonl`）— 独立子系统，暂不迁移
- QA 反馈 JSONL（`data/training/qa/*.jsonl`）— 独立子系统，暂不迁移
- 审计日志 JSONL — 设计上保持文件存储（哈希链防篡改）
- 前端代码 — 所有 API 接口保持不变

---

## 2. 现状分析

### 2.1 消息双写路径

```
add_message()
  ├─ 写入内存 (self.sessions)          ← 保留，实时推送需要
  ├─ _write_disk()  → JSONL 文件       ← 要删除
  └─ _write_db_sync() → SQLite         ← 保留，成为唯一持久化
```

### 2.2 历史读取路径

```
get_history(session_id, after_index)
  ├─ 查内存 → 有就返回                 ← 保留
  └─ _read_disk() → JSONL 文件         ← 改为查 SQLite
```

### 2.3 Session 元数据文件

```
{user_data}/claude-data/sessions/
  ├── {session_id}.jsonl        ← legacy session 文件，SQLite 启用后不再使用
  └── {session_id}.meta.json    ← 旧版 session title 存储，已在 sessions 表中
```

### 2.4 涉及的文件清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `src/message_buffer.py` | 修改 | 移除 `_write_disk`/`_read_disk`，`get_history` 增加 SQLite 回读 |
| `src/session_store.py` | 修改 | 删除 `_write_disk_session` 死代码 |
| `main_server.py` | 修改 | 删除所有 JSONL fallback 路径，修复 fork 端点 |
| `src/session_cleanup.py` | 替换 | 改为基于 SQLite 的清理逻辑 |
| `scripts/cleanup_stale_files.py` | 已存在 | 用于迁移后清理残留文件 |
| `tests/unit/test_message_buffer.py` | 修改 | 删除 JSONL 相关测试 |
| `tests/unit/test_message_buffer_db.py` | 修改 | 删除 `test_add_message_still_writes_to_disk` |
| `tests/unit/test_session_cleanup.py` | 重写 | 改为测试 SQLite 清理逻辑 |
| `tests/unit/test_session_fork.py` | 修改 | 删除 JSONL 文件操作相关测试 |

---

## 3. 设计方案

### 3.1 MessageBuffer 改造

#### 3.1.1 删除磁盘读写

删除以下方法：
- `_disk_path()` — JSONL 路径工厂
- `_write_disk()` — 追加 JSON 行到文件
- `_read_disk()` — 从 JSONL 文件读取

删除 `add_message()` 中的双写调用：
```python
# 删除这一行
self._write_disk(session_id, message)
```

#### 3.1.2 `get_history()` 增加 SQLite 回读

当前逻辑：
```python
def get_history(self, session_id, after_index=0):
    # 查内存 → 没有就查磁盘
```

改为：
```python
def get_history(self, session_id, after_index=0):
    buf = self._ensure_buf(session_id)
    messages = buf["messages"]
    base_index = buf.get("base_index", 0)
    local_index = after_index - base_index

    if local_index >= 0 and local_index < len(messages):
        return messages[local_index:]

    # 内存不足：优先从 SQLite 读取（如果 DB 已连接）
    if self.db is not None:
        return self._read_db_sync(session_id, after_index)

    # 无 DB 时保留 JSONL 兜底（向后兼容，未来可移除）
    return self._read_disk(session_id, after_index)
```

新增 `_read_db_sync()` 方法：
```python
def _read_db_sync(self, session_id: str, after_index: int = 0) -> list[dict]:
    """从 SQLite 读取历史消息（同步，用于 WebSocket 恢复路径）。"""
    if self.db is None or self._sync_conn is None:
        try:
            self._sync_conn = sqlite3.connect(str(self.db.db_path))
        except Exception:
            return []

    cursor = self._sync_conn.execute(
        "SELECT type, subtype, name, content, payload, usage "
        "FROM messages WHERE session_id = ? AND seq >= ? ORDER BY seq",
        (session_id, after_index),
    )
    rows = cursor.fetchall()
    result = []
    for row in rows:
        msg = {"type": row[0]}
        if row[1] is not None: msg["subtype"] = row[1]
        if row[2] is not None: msg["name"] = row[2]
        if row[3] is not None: msg["content"] = row[3]
        if row[4] is not None:
            parsed = json.loads(row[4])
            msg["payload"] = parsed
            # 字段映射逻辑（同 session_store.py）
            if msg["type"] == "file_result" and "data" in parsed:
                msg["data"] = parsed["data"]
            if msg["type"] == "tool_use":
                if "id" in parsed: msg["id"] = parsed["id"]
                if "input" in parsed: msg["input"] = parsed["input"]
        if row[5] is not None: msg["usage"] = json.loads(row[5])
        result.append(msg)
    return result
```

**关键决策**：`get_history()` 新增 SQLite 回读后，WebSocket 恢复/订阅路径不再依赖 JSONL 文件。

#### 3.1.3 保留向后兼容

- 当 `self.db is None` 时（无 SQLite 环境），保留 `_read_disk()` 和 `_write_disk()` 作为兼容回退
- 生产环境 SQLite 始终启用，JSONL 路径不会被触发
- 测试环境可以通过不设置 `db` 参数来测试文件模式

### 3.2 SessionStore 清理

删除 `_write_disk_session()` 死代码方法（无调用者）。

### 3.3 main_server.py 改造

#### 3.3.1 删除所有 JSONL fallback

以下端点的 `else` 分支（JSONL fallback）全部删除：

| 端点 | 当前 fallback | 删除后行为 |
|------|--------------|-----------|
| `POST /api/users/{user_id}/sessions` | 创建 JSONL 文件 | 仅 SQLite |
| `GET /api/users/{user_id}/sessions` | 扫描 `*.jsonl` | 仅 SQLite |
| `DELETE /api/users/{user_id}/sessions/{session_id}` | 删除 JSONL + meta.json | 仅 SQLite |
| `GET /api/users/{user_id}/sessions/{session_id}/history` | 读 JSONL | 仅 SQLite |
| `PATCH /api/users/{user_id}/sessions/{session_id}/title` | 写 meta.json | 仅 SQLite |

**风险控制**：添加启动检查，如果 `session_store is None` 则拒绝启动（而非静默降级为文件模式）：
```python
# 在 lifespan() 启动逻辑中
if session_store is None:
    raise RuntimeError("DATA_DB_PATH must be set — file-based storage is deprecated")
```

#### 3.3.2 修复 fork 端点

当前 `fork_session` 直接操作 JSONL 文件（第 1822-1829 行）：
```python
src_jsonl = src_sessions / f"{session_id}.jsonl"
if src_jsonl.exists():
    src_jsonl.rename(src_jsonl.with_name(f"{new_session_id}.jsonl"))
```

改为纯 DB 路径：
```python
@app.post("/api/users/{user_id}/sessions/{session_id}/fork")
async def fork_session(user_id: str, session_id: str) -> dict[str, str]:
    new_session_id = f"session_{user_id}_{time.time()}_{uuid.uuid4().hex[:8]}"

    # 1. 从源 session 复制消息到新 session（通过 MessageBuffer 内存路径）
    history = buffer.get_history(session_id)
    for msg in history:
        buffer.add_message(new_session_id, msg)

    # 2. 在 DB 中创建新 session 记录
    if session_store is not None:
        await session_store.create_session(user_id=user_id, session_id=new_session_id)
        # 复制 session 标题
        sessions = await session_store.list_sessions(user_id=user_id)
        src_session = next((s for s in sessions if s["session_id"] == session_id), None)
        if src_session and src_session.get("title"):
            await session_store.update_session_title(
                user_id=user_id, session_id=new_session_id, title=src_session["title"]
            )
    else:
        raise RuntimeError("session_store is required for fork")

    return {"session_id": new_session_id}
```

删除文件操作相关的代码（`src_jsonl.rename`, `shutil.copy2(meta_file)`）。

### 3.4 session_cleanup.py 改造

当前逻辑扫描 `*.jsonl` 文件并按年龄/大小删除。改为基于 SQLite：

```python
async def cleanup_old_sessions_db(
    db: Database,
    max_age_days: int = 30,
    max_total_mb: int = 500,
) -> dict[str, int]:
    """基于 SQLite 清理旧 session。"""
    import sqlite3

    cutoff = time.time() - (max_age_days * 86400)

    # Phase 1: 删除旧 session 的消息
    conn = sqlite3.connect(str(db.db_path))
    try:
        # 找到超期的 session
        cursor = conn.execute(
            "SELECT id FROM sessions WHERE last_active_at < ?", (cutoff,)
        )
        expired_sessions = [row[0] for row in cursor.fetchall()]

        evicted_by_age = 0
        for sid in expired_sessions:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            evicted_by_age += 1
        conn.commit()

        # Phase 2: 按大小清理（检查 messages 表总大小）
        cursor = conn.execute("SELECT SUM(length(payload)) FROM messages")
        total_bytes = cursor.fetchone()[0] or 0
        total_mb = total_bytes / (1024 * 1024)

        evicted_by_size = 0
        if total_mb > max_total_mb:
            # 删除最旧 session 的消息直到低于限制
            cursor = conn.execute(
                "SELECT id FROM sessions ORDER BY last_active_at ASC"
            )
            for row in cursor.fetchall():
                if total_mb <= max_total_mb:
                    break
                sid = row[0]
                cursor2 = conn.execute(
                    "SELECT COALESCE(SUM(length(payload)), 0) FROM messages WHERE session_id = ?",
                    (sid,)
                )
                session_bytes = cursor2.fetchone()[0]
                conn.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
                conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
                total_mb -= session_bytes / (1024 * 1024)
                evicted_by_size += 1
            conn.commit()

        # 统计剩余 session
        cursor = conn.execute("SELECT COUNT(*) FROM sessions")
        remaining = cursor.fetchone()[0]
    finally:
        conn.close()

    return {
        "evicted_by_age": evicted_by_age,
        "evicted_by_size": evicted_by_size,
        "remaining": remaining,
    }
```

### 3.5 迁移步骤

#### Step 1: 代码改造（不影响运行中的服务）

1. `message_buffer.py`: 添加 `_read_db_sync()`，`get_history()` 增加 SQLite 回读路径
2. `message_buffer.py`: 删除 `_write_disk()` 调用（在 `add_message()` 中）
3. `session_store.py`: 删除 `_write_disk_session()`
4. `main_server.py`: 删除所有 JSONL fallback 分支
5. `main_server.py`: 修复 `fork_session` 端点
6. `session_cleanup.py`: 改为 SQLite 清理逻辑

#### Step 2: 测试验证

1. 运行现有测试套件
2. 修改受影响的单元测试：
   - `test_message_buffer.py`: 删除/修改 `TestDiskPersistence` 中的 JSONL 测试
   - `test_message_buffer_db.py`: 删除 `test_add_message_still_writes_to_disk`
   - `test_session_cleanup.py`: 重写为 DB 版本
   - `test_session_fork.py`: 删除 JSONL 文件操作相关测试
3. 手动验证：
   - 发送消息 → 确认 SQLite 写入
   - 重启服务 → 确认历史恢复
   - 会话切换 → 确认历史加载
   - Session 删除 → 确认 DB 清理

#### Step 3: 清理残留文件

运行已有脚本 `scripts/cleanup_stale_files.py --confirm`：
```
删除: data/.msg-buffer/*.jsonl
删除: data/users/*/claude-data/sessions/*.jsonl
删除: data/users/*/claude-data/sessions/*.meta.json
```

#### Step 4: 移除向后兼容代码（可选，后续 PR）

当确认所有环境都使用 SQLite 后：
- 删除 `_read_disk()` / `_write_disk()` 方法
- 删除 `message_buffer.py` 中的 `base_dir` 参数
- 删除 `session_cleanup.py` 中对 `.msg-buffer` 目录的引用

---

## 4. 风险评估

| 风险 | 严重程度 | 缓解措施 |
|------|---------|---------|
| `get_history()` SQLite 回读路径字段映射错误 | 中 | 字段映射逻辑复用 `session_store.py` 已验证的代码 |
| 内存 eviction 后 SQLite 查询性能下降 | 低 | SQLite WAL 模式 + 唯一索引 `(session_id, seq)` 已优化 |
| 删除 JSONL fallback 后无 SQLite 环境无法运行 | 低 | 添加启动检查，拒绝无 DB 启动 |
| 测试覆盖不足 | 中 | 单元测试改造 + 手动验证 |
| fork 端点消息复制遗漏 | 低 | 已有 `buffer.get_history()` + `buffer.add_message()` 路径（内存 + SQLite 双路径） |

---

## 5. 验证清单

迁移完成后逐项验证：

- [ ] `data/.msg-buffer/` 目录不再有新 JSONL 文件生成
- [ ] 删除所有现有 JSONL 文件后，服务正常启动
- [ ] 发送消息 → SQLite `messages` 表有新记录
- [ ] 重启服务 → 历史消息正确恢复
- [ ] 会话切换 → 历史正确加载
- [ ] Session 删除 → SQLite 中对应记录被删除
- [ ] Session fork → 消息和标题正确复制
- [ ] 旧 session 清理 → 超期 session 被删除
- [ ] 所有单元测试通过
- [ ] `scripts/cleanup_stale_files.py` 清理后无残留

---

## 6. 与现有迁移计划的关系

本文档是 `docs/sqlite-data-migration-plan.md` Phase 4 的细化版本，专注于消息和 session JSONL 的替换。

整体迁移计划的依赖关系：

```
Phase 1 (MCP → SQLite)  ──┐
Phase 2 (Tasks → SQLite)  ─┼── Phase 4 (清理 JSONL)  ← 本文档
Phase 3 (Memory → SQLite)─┘
                              ↓
                          Phase 5 (测试)
```

本文档的工作可以与其他 Phase 并行进行，因为不涉及 MCP/Tasks/Memory 模块。

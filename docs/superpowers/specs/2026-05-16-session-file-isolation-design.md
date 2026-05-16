# Session 级文件隔离设计

**日期**: 2026-05-16
**状态**: Draft — 待审批

## Context

同一用户的多个 session 共享 `workspace/outputs/` 目录。当 Session A 和 Session B 同时生成文件时，存在**文件被错误归属到另一个 session** 的竞态条件。

现有 3 层保护（时间窗口 + 快照差分 + DB 回查）的致命漏洞：
1. 如果 Session A 的 rename 操作因 OSError 失败，文件无 DB 记录
2. Session B 的后续扫描可以成功 rename 同一文件
3. DB 回查失效（因为只有 Session B 的记录）
4. 结果：文件永久归属到错误的 session

根本原因：`_task_locks` 是 per-session 的，同用户的不同 session 之间**没有任何 workspace 级别的锁**，共享目录上的文件操作存在竞态。

## 方案：永久性 Session 子目录

### 目录结构

```
data/users/{user_id}/workspace/
  .claude/                  ← 共享（skills, memory）
  memory/                   ← 共享
  outputs/                  ← 所有产物统一入口
    sess_a1b2c3d4e5f6/      ← Session A 专属
      report.pdf
    sess_f6e5d4c3b2a1/      ← Session B 专属
      report.pdf            ← 同名不冲突
```

### 核心原则

**文件归属 = 物理目录路径**。不再需要时间窗口、快照、DB 回查。

- Session A 的文件写入 `outputs/sess_a1b2c3d4/`
- Session B 的文件写入 `outputs/sess_f6e5d4c3/`
- 两个目录互不干扰，同名文件不会冲突

### Agent 工作目录

**CWD 保持为 `{user_id}/workspace/`**（不改为 session 子目录）：

- Agent 仍可跨 session 读取文件
- `.claude/` 和 `memory/` 不受影响
- 只在文件写入/扫描阶段做 session 级隔离，所有产出统一归入 `outputs/{session_id}/`

### 改动范围

#### 1. Session 创建时建立 outputs 子目录

**文件**: `main_server.py` — `create_session` 相关代码（~line 3372）

Session 创建（HTTP 端点或 WebSocket 首次消息）时：
```python
session_dir = user_workspace_dir(user_id) / "outputs" / session_id
session_dir.mkdir(parents=True, exist_ok=True)
```

#### 2. 引导 Agent 写入 session 目录

**文件**: `main_server.py` — `_format_first_message_prompt()` (~line 1880) + `_build_history_prompt()`

Agent 的 CWD 保持为 `{user_id}/workspace/`，需要在 prompt 中告知输出路径：
```
Your working directory is: /data/users/{user_id}/workspace/
All generated files must be written to: outputs/{session_id}/
```

**Write 工具拦截兜底**（~line 2231）：如果 agent 写入 `outputs/` 下的路径不含 session 前缀，自动重定向：
```python
# Normalize: outputs/report.pdf → outputs/{session_id}/report.pdf
# Also handle: report.pdf → outputs/{session_id}/report.pdf
if not file_path.startswith(f"outputs/{session_id}/"):
    if file_path.startswith("outputs/"):
        file_path = f"outputs/{session_id}/{file_path[len('outputs/'):]}"
    else:
        file_path = f"outputs/{session_id}/{file_path}"
```

**Bash 命令改写**（`src/workspace_enforcement.py`）：同样在路径前注入 session 目录前缀，确保 agent 通过 shell 创建的文件也写入正确位置。

#### 3. 大幅简化 `_scan_workspace_for_generated_files`

**文件**: `main_server.py` — `_scan_workspace_for_generated_files()` (~line 613)

当前逻辑（100+ 行）：扫描整个 workspace，时间窗口过滤，快照差分，DB 回查。

简化为（~20 行）：只扫描 `outputs/{session_id}/` 目录。
```python
def _scan_workspace_for_generated_files(
    workspace: Path,
    user_id: str,
    session_id: str,
) -> list[dict[str, Any]]:
    """Scan only the session's outputs/{session_id}/ directory for generated files."""
    session_outputs = workspace / "outputs" / session_id
    if not session_outputs.exists():
        return []

    files = []
    for f in session_outputs.rglob("*"):
        if not f.is_file() or not should_include_generated_file(f.name):
            continue
        rel = f.relative_to(workspace).as_posix()  # e.g. "outputs/sess_abc123/report.pdf"
        size = f.stat().st_size
        download_url = build_download_url(user_id, rel)
        files.append({
            "filename": rel,
            "stored_name": f.name,
            "size": size,
            "download_url": download_url,
        })
        _insert_generated_file(user_id, session_id, rel, f.name, size, rel)

    return files
```

**不再需要的参数**: `workspace_snapshot`, `start_time`, `task_end`, `existing_files`。

#### 4. `_insert_generated_file` 路径更新

**文件**: `main_server.py` — `_insert_generated_file()` (~line 576)

`rel_path` 现在包含 `outputs/` + session 前缀（如 `outputs/sess_abc123/report.pdf`），下载 URL 自动正确：
```
/api/users/{user_id}/download/outputs/sess_abc123/report.pdf
```

#### 5. 下载端点路径验证

**文件**: `main_server.py` — 下载端点 (~line 3785)

当前路径验证：`file_path` 必须在 `workspace` 内。
改为：`file_path` 必须在 `{user_id}/workspace/` 内的任意子目录中。

对于新格式路径（`outputs/sess_*/...`），路径前缀与现有 `outputs/` 检查天然兼容。旧格式路径（纯 `outputs/filename`）通过回退逻辑兼容。

#### 6. 容器模式

**文件**: `main_server.py` — `run_agent_task_container()` (~line 2440)

- `workspace_snapshot` 不再需要
- `_scan_workspace_for_generated_files` 调用同上简化
- 容器 volume 挂载路径不变（workspace 整体挂载）
- Agent prompt 中同样包含 `outputs/{session_id}/` 路径指引

#### 7. 文件列表 API

**现有端点**不受影响：
- `GET /api/users/{user_id}/sessions/{session_id}/files` — 查询 DB `WHERE session_id = ?`，URL 由 DB 记录提供
- `GET /api/users/{user_id}/files` — 查询所有文件（跨所有 session）

DB schema 不变，`stored_name` 字段现在包含 session 路径前缀。

### 不需要改的

| 组件 | 原因 |
|------|------|
| DB schema | `generated_files` 和 `uploads` 表已有 `session_id` 字段 |
| 前端 | 下载 URL 由后端生成，前端只消费 URL |
| 上传逻辑 | 上传文件通过 UUID 重命名已避免冲突，不受影响 |
| WebSocket 消息流 | 文件通过 URL 下发，与路径无关 |

### 迁移策略

**现有文件**：保持不动。旧 session 的文件仍在 `outputs/` 根目录，其 DB 记录的 `stored_name` 不含 session 路径前缀。下载端点兼容两种路径格式：
1. 先尝试 `{workspace}/{file_path}`（新格式，如 `outputs/sess_abc123/report.pdf`）
2. 如果不存在，回退到 `{workspace}/outputs/{filename}`（旧格式，如 `outputs/report.pdf`）

**新 session**：创建时自动在 `outputs/` 下建立 `outputs/{session_id}/` 子目录。

### 风险分析

| 风险 | 缓解措施 |
|------|---------|
| 旧 session 无法读取历史文件 | 旧路径通过回退逻辑兼容 |
| outputs/ 子目录积累占用磁盘 | 可通过定期清理策略处理，与现有 session 生命周期一致 |
| Agent 在 outputs 目录外写文件 | Write 工具拦截兜底会自动重定向到 `outputs/{session_id}/`；`is_path_within_user_dir` 限制在 workspace 内 |
| Agent 往 outputs/ 根目录写文件 | Write 拦截自动注入 session 前缀 |
| 容器模式下路径不一致 | volume 挂载路径不变，session 目录在容器内外路径一致 |

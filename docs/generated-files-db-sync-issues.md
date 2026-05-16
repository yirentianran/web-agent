# 生成文件与数据库同步问题分析

> 分析日期: 2026-05-16
> 分析范围: `generated_files` 表与 `uploads` 表的同步机制

## 正常同步流程

```
agent 任务结束
  ↓
_scan_workspace_for_generated_files() 扫描 workspace
  ↓
_process_file() 重命名文件 + 生成 UUID stored_name
  ↓
_insert_generated_file() 写入 generated_files 表 (main_server.py:576-604)
  ↓
_emit_file_result() 推送 file_result 消息到前端
```

每次扫描发现的生成文件都会同步写入 DB，连接是直接的、同步的。上传文件在用户发送消息时写入 `uploads` 表 (main_server.py:752-823)。

## 已知问题

### 问题 1: DB 写入失败完全静默

**位置**: `main_server.py` — `_insert_generated_file()` 第 576-604 行, `_insert_upload_file()` 第 752-823 行

**问题**: 两个 insert 函数都使用了 `try ... except Exception: pass` 包裹 DB 操作。任何数据库写入失败（锁冲突、磁盘满、权限问题等）都不会报错、不会记录日志、不会触发重试。

**影响**: 文件已写入磁盘但数据库无记录时，前端无法发现这些文件，且运维人员无从排查。

### 问题 2: mime_type 列永远为空

**位置**: `main_server.py` — 同上两个 insert 函数

**问题**: 数据库 schema (`src/database.py` 第 163-193 行) 定义了 `mime_type TEXT NOT NULL DEFAULT ''` 列，但两个 insert 函数都从未传入 `mime_type` 值，该列始终为空字符串。

**影响**: 前端下载时无法自动识别 Content-Type，依赖扩展名推断或硬编码映射。

### 问题 3: generated-files 接口绕过数据库

**位置**: `main_server.py` — `GET /api/users/{user_id}/generated-files` 端点 (第 3511-3541 行)

**问题**: 该接口直接扫描 `outputs/` 目录的文件系统，完全不查询 `generated_files` 表。

**影响**:
- 返回的结果可能与 DB 记录不一致（磁盘有但 DB 无，或 DB 有但磁盘已删）
- 与 `GET /api/users/{user_id}/sessions/{session_id}/files` 等走 DB 查询的接口行为不统一
- 如果同一文件在不同 session 间共享，无法正确归属

### 问题 4: 上传文件 DB 记录延迟

**位置**: `main_server.py` — HTTP 上传端点 (第 3692-3739 行) vs WebSocket 消息处理 (第 3081 行)

**问题**: 文件上传到服务器时只写入磁盘 (`workspace/uploads/{stored_name}`)，数据库记录要等到用户发送消息并附带该文件时才创建。

**影响**:
- 用户上传文件后如果放弃发送消息，uploads 表无记录但磁盘上有残留文件
- 在上传完成到发送消息之间，文件列表接口无法显示刚上传的文件
- 无法统计"上传但未使用"的文件数量

## 相关代码文件

| 文件 | 内容 |
|------|------|
| `main_server.py` | 文件扫描、插入、API 端点全链路 |
| `src/database.py` | SQLite schema (`generated_files` / `uploads` 表定义) |
| `src/workspace_enforcement.py` | 路径验证与重写共享模块 |
| `agent_server.py` | 容器模式 agent 运行器（无文件追踪逻辑） |
| `src/container_bridge.py` | 容器 WebSocket 桥接 |

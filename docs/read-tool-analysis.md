# Read 工具实现分析

## 实际执行者

两种模式下，Read 操作都不是 `main_server.py` 或 `agent_server.py` 自己执行的，而是由 **bundled Claude CLI 二进制文件**（`.venv/lib/python3.12/site-packages/claude_agent_sdk/_bundled/claude`）内部实现。Python 层的角色是拦截/过滤（PreToolUse hook）和后处理（truncation）。

---

## 非容器模式

### PreToolUse Hook（`main_server.py` `read_path_hook`）

在 `build_sdk_options()` 中定义的 `read_path_hook`，已注册到 hooks dict：

```python
hooks = {
    "PreToolUse": [
        HookMatcher(matcher="Write", hooks=[write_path_hook]),
        HookMatcher(matcher="Bash", hooks=[bash_path_hook]),
        HookMatcher(matcher="Read", hooks=[read_path_hook]),
    ],
}
```

Hook 执行两步检查：

1. **敏感文件拦截** — 调用 `FileAccessFilter.check(file_path)`，拒绝 14 种敏感文件模式
2. **文件大小限制** — 通过 `os.stat()` 检查文件大小，超过 `MAX_READ_FILE_BYTES`（默认 20MB）则 deny，提示用户用 `head`/`split` 分段处理

路径解析：相对路径以 workspace 目录为基准。

### `can_use_tool_cb` 中的 Read 检查

`can_use_tool_cb` 中仍有 `FileAccessFilter` 的 Read 检查代码，但 `acceptEdits` 模式下 CLI 对 Read 不触发 permission prompt，所以该回调路径不会被调用。Read 的安全过滤完全依赖 PreToolUse hook —— hook 不受 permission_mode 影响，始终触发。

---

## 容器模式

### PreToolUse Hook（`agent_server.py`）

Read 已注册到 hooks 配置和回调映射：

```python
hooks_config = {
    "PreToolUse": [
        {"matcher": "Write", "hookCallbackIds": ["__hook_write__"]},
        {"matcher": "Bash", "hookCallbackIds": ["__hook_bash__"]},
        {"matcher": "Read", "hookCallbackIds": ["__hook_read__"]},
    ],
}

hook_callbacks = {"__hook_write__": "Write", "__hook_bash__": "Bash", "__hook_read__": "Read"}
```

Hook handler 中执行两步检查（与非容器模式一致）：

1. **敏感文件拦截** — `FileAccessFilter.check(file_path)`
2. **文件大小限制** — `Path.stat()` 检查，超过 `MAX_READ_FILE_BYTES`（默认 20MB）则 deny

路径解析：相对路径以 `self._cwd`（容器内 workspace）为基准。`OSError`（文件不存在）静默放过，由 CLI 自行报错。

---

## Read 文件大小限制

| 配置项 | 位置 | 默认值 | 说明 |
|--------|------|--------|------|
| `MAX_READ_FILE_BYTES` | `src/constants.py` | **20MB** | 通过环境变量可覆盖。Read 前通过 `os.stat()` 检查，超限则拒绝并提示分段处理 |

---

## Tool Result 截断

| 层级 | 位置 | 默认值 | 说明 |
|------|------|--------|------|
| 当前轮截断 | `src/truncation.py` | **50000 chars** | `truncate_tool_output()`，保留头部 ~49800 chars + 统计摘要。两种模式均触发（>1000 chars 时） |
| 多轮历史截断 | `main_server.py` | **500 chars** | `_TOOL_RESULT_MAX_CHARS`，仅影响历史上下文，不影响当前返回 |
| 日志截断 | `src/agent_logger.py` | **8000 chars** | 仅影响日志记录 |

---

## 对比总结

| 方面 | 非容器模式 | 容器模式 |
|------|-----------|----------|
| PreToolUse Hook for Read | **有** — `read_path_hook` | **有** — `__hook_read__` |
| FileAccessFilter 拦截 | **是** — via hook | **是** — via hook |
| 文件大小限制 | **是** — 20MB 默认 | **是** — 20MB 默认 |
| 路径解析基准 | workspace 目录 | 容器内 `self._cwd` |
| 敏感文件拦截（.env, .claude/ 等） | **是** — 14 种模式 | **是** — 14 种模式 |
| Tool result 截断 | **是** — 50000 chars | **是** — 50000 chars |
| 多轮历史截断 | 500 chars | 500 chars |

---

## FileAccessFilter 拒绝模式

定义于 `src/security_filter.py:160-196`，共 14 个模式，现已通过 PreToolUse hook 对 Read 生效：

| 模式 | 说明 |
|------|------|
| `\.env(\.\w+)?$` | 环境变量文件 |
| `\.claude/` | Claude 配置目录 |
| `CLAUDE\.md$` | Claude 指令文件 |
| `AGENTS\.md$` | Agent 指令文件 |
| `settings\.json$` | 配置文件 |
| `Dockerfile` | Docker 构建文件 |
| `docker-compose` | Docker Compose 文件 |
| `\.(conf\|cfg\|ini\|yaml\|yml)$` | 各类配置文件 |
| `\.git/config$` | Git 配置 |
| `pyproject\.toml$` | Python 项目配置 |
| `package(-lock)?\.json$` | Node 项目配置 |
| `uv\.lock$` | Python 锁文件 |
| `\.(pem\|key\|crt)$` | 密钥和证书文件 |

---

## 关键文件

| 文件 | 相关内容 |
|------|---------|
| `src/constants.py` | `MAX_READ_FILE_BYTES`（20MB） |
| `src/security_filter.py:160-196` | `FileAccessFilter` 定义，14 个拒绝模式 |
| `src/truncation.py` | `truncate_tool_output`，`MAX_TOOL_OUTPUT_CHARS`（50000） |
| `main_server.py` `read_path_hook` | 非容器模式 Read hook：敏感文件 + 大小检查 |
| `agent_server.py` hook 注册 + handler | 容器模式 Read hook：敏感文件 + 大小检查 |
| `src/container_bridge.py` | 容器模式 tool_result 截断 |

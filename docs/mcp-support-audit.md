# MCP Support Audit

## 架构定位

Web Agent 是 **MCP Client**，不是 MCP Server。它连接到外部 MCP Server，将它们的 Tool / Resource / Prompt 交给 `claude-agent-sdk` 调用。MCP 协议层能力发现（`list_tools` / `list_resources` / `list_prompts` / `send_ping`）由本项目的 `_connect_and_discover_mcp()` 完成，并将结果持久化到 SQLite。Tool 调用委托给 SDK。

## MCP 协议能力覆盖

| 能力 | 状态 | 说明 |
|------|------|------|
| Tool（列出） | 完整 | `list_tools()` → SQLite 持久化，格式 `mcp__{server}__{tool}`，自动发现、差异同步 |
| Tool（调用） | 完整 | 委托给 `claude-agent-sdk` |
| Resource（列出） | 完整 | `list_resources()` → SQLite 持久化，自动发现，try/except 降级（旧 Server 兼容） |
| Prompt（列出） | 完整 | `list_prompts()` → SQLite 持久化，自动发现，try/except 降级（旧 Server 兼容） |
| Ping | 完整 | `send_ping()` 在每次状态检查时调用 |
| Sampling | 未实现 | — |
| Roots | 未实现 | — |
| Logging | 未实现 | — |

## 传输方式覆盖

| 传输 | 状态 | 说明 |
|------|------|------|
| stdio | 完整 | `mcp.client.stdio.stdio_client`，子进程管理、env 透传、30s 超时 |
| SSE | 完整 | `mcp.client.sse.sse_client`，MCP 协议握手，能力发现 |
| Streamable HTTP | 完整 | `mcp.client.streamable_http.streamable_http_client`，MCP 协议握手，能力发现 |
| HTTP（旧版） | 部分 | 配置层保留 `type: "http"`，实际不推荐使用，建议改用 `streamable_http` |

> **注意**：SDK CLI（`claude-agent-sdk`）仅识别 `stdio` / `sse` / `http` 三种 type。`streamable_http` 在 `_build_sdk_config()` 中被映射为 `"http"` 再传给 CLI，以确保兼容性。

## 统一发现引擎

`main_server.py: _connect_and_discover_mcp()` 根据 `cfg["type"]` 路由到对应传输的 `mcp` SDK 客户端：

```
stdio              → stdio_client(cmd, args, env)
sse                → sse_client(url, headers)
streamable_http    → streamable_http_client(url, headers)
```

`_discover_all()` 统一执行 Ping + 列出 Tools / Resources / Prompts，结果由 `_sync_discovery_to_db()` 与 DB 现有数据比较后按差异写入。

## 关键代码文件

| 文件 | 职责 |
|------|------|
| `src/mcp_store.py` | MCP Server 配置的 SQLite CRUD 存储（含 headers / resources / prompts 列） |
| `src/models.py` (L104-147) | `McpServerConfig` Pydantic 模型（含 resources / prompts / headers 字段，null→[] 强制转换） |
| `src/database.py` | 数据库 schema 与迁移（`migrate_v3` 添加 headers / resources / prompts 列） |
| `src/constants.py` | 内置工具列表；WebSearch/WebFetch 默认禁用，由 MCP 替代 |
| `src/container_manager.py` (L157-188) | 通过 `MCP_CONFIG_JSON` 环境变量注入容器 |
| `main_server.py` (~L1115-1584) | MCP 配置加载、`_build_sdk_config`（含 streamable_http→http 映射） |
| `main_server.py` (~L5706-5965) | MCP REST API：CRUD、发现工具/资源/提示、状态检查、启停 |
| `main_server.py` (~L5750-5850) | `_connect_and_discover_mcp` / `_discover_all` / `_sync_discovery_to_db` |
| `agent_server.py` (L157-159) | 容器模式传递 `--mcp-config` |

## 前端

| 文件 | 职责 |
|------|------|
| `frontend/src/lib/types.ts` (L85-130) | `McpServer` TypeScript 类型（含 resources / prompts / headers） |
| `frontend/src/hooks/useMCPServers.ts` | MCP API React Hook |
| `frontend/src/components/MCPPage.tsx` | MCP 管理页面（JSON 编辑器、CRUD、发现、启停；`inferType` 自动推断 type） |
| `frontend/src/i18n/en.json` / `zh.json` | 国际化字符串（含 resourceCount / promptCount） |

### 前端类型推断

`MCPPage.tsx: jsonToServer()` 使用 `inferType()` 辅助函数根据字段自动推断 MCP Server type：

- 有 `url` 且无 `type` → `streamable_http`
- 有 `command` 且无 `type` → `stdio`
- 两者皆无 → `stdio`

这解决了用户粘贴 MCP config 格式 JSON（例如 `{"mcpServers": {"name": {"url": "...", "headers": {...}}}}`）时因缺少 `type` 字段导致的 422 错误。

## MCP REST API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/admin/mcp-servers` | GET | 列出所有 Server（含 tools / resources / prompts 列表） |
| `/api/admin/mcp-servers` | POST | 注册新 Server，自动发现 Tool / Resource / Prompt |
| `/api/admin/mcp-servers/{name}` | PUT | 更新 Server 配置 |
| `/api/admin/mcp-servers/{name}/discover-tools` | POST | 重新连接并发现所有能力（所有传输类型均支持） |
| `/api/admin/mcp-servers/{name}` | DELETE | 删除 Server |
| `/api/admin/mcp-servers/{name}/toggle` | PATCH | 启用 / 禁用 |
| `/api/admin/mcp-servers/status` | GET | 所有 Server 连接状态（含 resource_count / prompt_count） |

## 仪表盘时间范围

`/_/dashboard` 的 `TimeRangeSelector` 组件支持在自定义范围内使用 `datetime-local` 输入选择日内时间范围（例如 `2026-05-20T10:00` 至 `2026-05-20T14:00`），以便进行 5 分钟粒度查询。后端通过 `datetime.fromisoformat()` 解析日期时间字符串，并在无时间组件时回退到午夜/23:59:59 边界，确保向后兼容。

## 已知局限

1. **全局共享**：MCP Server 对所有用户共享（`access` 列存在但运行时不读取）
2. **Resource / Prompt 不可调用**：列出的能力和数量可在 UI 中查看，但 SDK CLI 不支持将 Resource/Prompt 传递给模型
3. **SDK type 映射**：`streamable_http` 在传给 CLI 时被映射为 `http`（CLI 不原生识别 `streamable_http`）

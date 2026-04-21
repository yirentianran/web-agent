# Web Agent 消息流 vs IM 消息流 — 对比分析

## 概述

本文档对比 Web Agent 项目的 ChatArea 消息流与即时通讯（IM）系统（微信、WhatsApp、Telegram、Slack、Discord）的消息流设计，找出差异与可借鉴之处。

---

## 1. 消息排序

| 维度 | IM 系统 | Web Agent 当前实现 |
|------|---------|-------------------|
| 排序依据 | 服务端单调递增 ID（msg_id / Snowflake / pts） | 后端分配的 `index` 字段 |
| 客户端排序逻辑 | 服务端保证顺序，客户端按 ID 排序 | `sortedMessages = [...messages].sort((a, b) => a.index - b.index)` |
| 时钟漂移处理 | 完全不信任客户端时间，服务端为唯一真相 | 不依赖时间戳，`index` 由后端分配 |
| 乐观消息排序 | 分配临时负数 ID 或 UUID，服务端确认后替换 | 分配 `lastBackendIndex - 1`（负数），确保排在重播消息之前 |

### 评价

Web Agent 的 `index` 方案与 IM 的单调递增 ID **本质上相同**，都是服务端单调递增序列号。这是一个正确的选择。

### 差异

- IM 系统通常在 **会话级别** 分配 `msg_id`（每个聊天独立递增），而 Web Agent 的 `index` 是 **session 级别** 的，每个 session 独立递增。两者一致。
- IM 系统通常有持久化存储（SQLite / LocalStorage）作为消息缓存，Web Agent 依赖 REST API 拉取历史消息，无本地持久化存储。

---

## 2. 消息发送状态

| 维度 | IM 系统 | Web Agent 当前实现 |
|------|---------|-------------------|
| 状态机 | `pending → sending → sent → delivered → read / failed` | `pending → running → completed / error / waiting_user` |
| 乐观渲染 | 立即显示用户消息 + 发送中图标 | 立即显示用户消息，不显示发送中图标 |
| 失败回退 | 红色感叹号 + 重试按钮 | 无重试按钮，需手动重新发送 |
| 服务端确认 | 匹配 `client_msg_id` 或 `msg_id` | 按内容匹配 `content` 清除 pending |

### 评价

Web Agent 缺少 **发送失败反馈**。当消息发送失败时（网络断开、服务端错误），用户看不到任何提示，消息会永久停留在乐观渲染状态。

### 建议改进

```
发送流程改进：
1. 用户发送消息 → 乐观渲染 + "发送中..." 状态
2. WebSocket 发送成功 → 移除 "发送中..."
3. 发送失败 → 显示 "发送失败" + 红色标记 + 重试按钮
4. 添加发送超时机制（如 30s 无确认则标记失败）
```

---

## 3. 离线/断线恢复

| 维度 | IM 系统 | Web Agent 当前实现 |
|------|---------|-------------------|
| 恢复机制 | 客户端保存 `last_seen_seq`，重连后请求 `> last_seen_seq` 的消息 | `sendRecover(sessionId, computeRecoverIndex(messages))` |
| 游标跟踪 | 持久化存储（SQLite / LocalStorage），页面刷新不丢失 | 内存中的 `messages` 数组，页面刷新丢失 |
| 恢复粒度 | 按会话级别增量同步 | 按 session 级别恢复（`computeRecoverIndex` 使用 `max(index) + 1`） |
| 心跳驱动检测 | 心跳超时触发恢复（Discord 41.25s, WhatsApp 30s） | 心跳超 60s 触发恢复（`lastHeartbeatRef`），每 10s 检查 |
| WebSocket 断线恢复 | 自动重连 + 续传（Discord resume session, Telegram TDLib） | 自动重连（最多 5 次，指数退避）+ 全量恢复（index 0） |

### 评价

Web Agent 的恢复机制 **已覆盖核心场景**，相比文档初版有以下进展：

1. **心跳失活检测已实现**：当 session 处于 `running` 状态且 60s 未收到心跳时，自动触发增量恢复。这解决了 "Agent 已完成但前端卡在工作状态" 的问题。
2. **恢复索引计算已修复**：`computeRecoverIndex` 使用 `max(index) + 1` 而非 `messages.length`，正确处理非连续索引。
3. **会话切换恢复**：切换后使用 `computeRecoverIndex(messages)` 从最后已知索引增量恢复。

### 仍存在的差距

1. **页面刷新后消息丢失**：IM 系统将消息持久化到 SQLite/LocalStorage，刷新后从本地加载再增量同步。Web Agent 每次刷新都重新拉取历史消息。
2. **WebSocket 断线重连仍全量恢复**：重连后从 `index 0` 开始，对长 session 不友好。应改为从 `last_known_index` 增量恢复（需要持久化记录 last_index）。
3. **心跳间隔**：服务端 30s 发送心跳，客户端 60s 阈值检测。IM 系统通常用更短的心跳间隔（15-30s），检测更快。

---

## 4. 会话切换

| 维度 | IM 系统 | Web Agent 当前实现 |
|------|---------|-------------------|
| 切换速度 | 本地缓存即时加载，后台增量同步 | REST 拉取历史消息 + WebSocket 恢复增量 |
| 状态保持 | 每个会话独立维护消息、滚动位置、已读状态 | 每个会话独立维护消息、滚动位置、sessionState |
| 后台同步 | 切换后持续监听新消息（WebSocket 订阅） | 切换后 `sendRecover` 获取增量，WebSocket 持续推送 |
| 滚动位置 | 记住上次阅读位置（SQLite） | `scrollPositions` Map + localStorage |

### 评价

Web Agent 的会话切换流程 **已经比较完善**：

```
handleSelectSession 流程：
1. setActiveSession(id) + 同步 ref
2. 重置心跳计时器（防止误触发恢复）
3. 恢复 pending 消息（如有）
4. REST 拉取历史消息（SQLite 路径）
5. 合并 pending 与历史消息
6. 从 DB 推导 sessionState + 合并 live buffer
7. sendRecover 获取切换期间的增量消息
```

### 差异

- IM 系统通常有 **预加载** 机制（提前缓存最近 50 条消息），切换时几乎无延迟。Web Agent 每次切换都需要等待 REST 响应（已从 SQLite 读取，性能较好）。
- IM 系统的 WebSocket 通常 **订阅所有会话** 的事件，Web Agent 的 WebSocket 也是接收所有 session 消息，然后通过 `session_id` 过滤。两者一致。
- Web Agent 在会话切换时会重置心跳计时器，防止因旧 session 的心跳缺失误触发恢复。

---

## 5. 实时状态指示

| 维度 | IM 系统 | Web Agent 当前实现 |
|------|---------|-------------------|
| 在线状态 | 在线/离线/忙碌（WebSocket 连接状态） | WebSocket 连接状态（connected 标志） |
| 输入指示 | "对方正在输入..."（typing 事件，6s 自动消失） | "Agent is working..." + 计时器 |
| 心跳/保活 | 应用层 ping/pong（30-60s 间隔） | 服务端每 10s 发送 `heartbeat` 消息 |
| 超时检测 | 心跳超时间隔 2x ping 间隔 | 心跳超 60s 触发恢复 |
| 计时器 | 不适用 | 每 session 独立计时（`sessionStartTimesRef`） |

### 评价

Web Agent 的 "Agent is working..." 状态类似于 IM 的 typing indicator，但有本质区别：

- **IM typing**：是短暂的、事件驱动的（按键触发，6s 自动消失）
- **Agent working**：是持久的、任务驱动的（直到任务完成才消失）

这是一个正确的设计差异。Agent 的 "working" 状态承载的信息比 typing 更多。

---

## 6. 消息去重

| 维度 | IM 系统 | Web Agent 当前实现 |
|------|---------|-------------------|
| 去重策略 | ID-based Set 检查 + 数据库唯一约束 | 多层去重（见下表） |
| 重播消息去重 | 服务端 `msg_id` 唯一性保证 | `msg.replay === true` 时按 index 去重 |
| 乐观消息去重 | `client_msg_id` 匹配 | 按 `content` 内容匹配 |
| 实时消息去重 | 服务端 ID 唯一性 | 按 `index` 去重 |

### Web Agent 的四层去重

```typescript
// 1. 首轮重播去重：已存在相同 index 则跳过
if (isFirstTurnMessage && prev.some(m => m.index === msg.index)) return prev

// 2. 重播消息去重：replay 标记 + 相同 index
if (msg.replay && prev.some(m => m.index === msg.index)) return prev

// 3. 实时用户消息去重：按内容匹配（防乐观重复）
if (msg.type === 'user' && !msg.replay) {
  if (prev.some(m => m.type === 'user' && m.content === msg.content)) return prev
}

// 4. 实时非用户消息去重：按 index 匹配
if (msg.index != null && prev.some(m => m.index === msg.index)) return prev
```

### 评价

Web Agent 的去重策略 **设计合理**，但存在一个潜在问题：

- **用户消息内容去重**（策略 3）：如果用户发送两条内容完全相同的消息，第二条会被错误地去重。IM 系统用 `client_msg_id`（客户端生成的 UUID）来区分每次发送，更可靠。

### 建议

```
改进用户消息去重：
- 为每条乐观消息生成临时 UUID（client_msg_id）
- 发送时将 UUID 附加到消息 payload
- 服务端回显时带上 UUID，前端按 UUID 匹配
- 去重策略从 "按内容匹配" 改为 "按 UUID + index 匹配"
```

---

## 7. 消息分页/懒加载

| 维度 | IM 系统 | Web Agent 当前实现 |
|------|---------|-------------------|
| 分页方式 | 游标分页（before/after cursor） | 无分页，一次性加载全部历史 |
| 每次加载量 | 50-100 条/页 | 全部历史消息 |
| 滚动懒加载 | 滚到顶部触发加载更多 | 不支持 |
| 跳转消息 | Telegram: 跳到日期, Discord: around cursor | 不支持 |

### 评价

对于 Agent 场景，单次对话通常不超过几十条消息，无分页设计 **目前是合理的**。但如果未来支持长会话（如代码审查、大型项目生成），建议引入：

```
建议的懒加载方案：
1. 首次加载：最近 50 条消息
2. 滚动到顶部：触发加载更早的消息
3. 使用 index 游标：GET /history?before={lastIndex}&limit=50
4. 总消息数指示："已加载 50/200 条消息"
```

---

## 8. 架构对比总结

### 消息流架构图

#### IM 系统典型架构

```
┌──────────┐    ┌─────────────┐    ┌──────────┐
│  客户端   │◄──►│  消息队列    │◄──►│  数据库   │
│          │    │  (Kafka等)   │    │ (SQLite) │
├──────────┤    └─────────────┘    └──────────┘
│ UI 渲染  │         │
│ 乐观更新 │    ┌─────┴─────┐
│ 本地缓存 │◄──►│  WebSocket │
│ (SQLite) │    │  Gateway  │
└──────────┘    └───────────┘
                    │
              ┌─────┴─────┐
              │  消息存储  │
              │  推送服务  │
              └───────────┘
```

#### Web Agent 当前架构

```
┌──────────┐    ┌──────────────┐    ┌──────────────────────────────────┐
│  React   │◄──►│  FastAPI     │◄──►│  内存 (实时推送)                   │
│  前端     │    │  后端         │    └──────────────────────────────────┘
├──────────┤    └──────────────┘                ↓
│ ChatArea │         │                   ┌──────────────┐
│ 消息数组 │    ┌─────┴─────┐            │  SQLite 数据库 │ ← 唯一持久化层
│ (内存)   │◄──►│  WebSocket │            │  (messages 表) │   REST API +
└──────────┘    │  Bridge   │            └──────────────┘   WebSocket 恢复
                    │
              ┌─────┴─────┐
              │ Claude    │
              │ Agent SDK │
              └───────────┘
```

### 核心差异

| 维度 | IM 系统 | Web Agent |
|------|---------|-----------|
| 消息持久化 | 本地 SQLite/LocalStorage | 内存（页面刷新丢失）|
| 消息存储后端 | 分布式数据库 | **两层混合**：内存（实时）+ SQLite（持久化 + 崩溃恢复）|
| 离线能力 | 完整（本地缓存 + 增量同步） | 有限（需在线拉取历史）|
| 推送机制 | 主动推送 + 消息队列 | WebSocket 直连 Agent |
| 消息路由 | 消息队列 → 推送服务 → 客户端 | WebSocket Bridge 直连 |

### 存储架构详解（已统一）

| 层级 | 存储介质 | 用途 | 谁在读取 |
|------|---------|------|---------|
| **1. 内存** | `MessageBuffer.sessions` (Python dict) | 实时推送、活跃 session 消息缓存 | WebSocket 订阅循环 (`buffer.get_history()`) |
| **2. SQLite** | `data/web-agent.db` → `messages` 表 | 持久化存储、REST API + WebSocket 历史查询 | `session_store.get_session_history()` + `buffer._read_db_sync()` |

**已完成的变更**：`buffer.get_history()` 现在优先读取 SQLite（`_read_db_sync()`），内存未命中时直接回读数据库。JSONL 文件不再被写入（`_write_disk()` 已从 `add_message()` 移除，成为死代码），仅作为最终兼容兜底存在。

### 历史遗留文件

以下 JSONL 文件仍可能存在于磁盘，但已不再被主动使用：

| 路径 | 状态 | 说明 |
|------|------|------|
| `data/.msg-buffer/{session_id}.jsonl` | **已废弃** | 旧版消息缓冲区文件，SQLite 启用后不再写入 |
| `data/users/*/claude-data/sessions/*.jsonl` | **已废弃** | 旧版 session 文件，session_store 已纯 DB 路径 |
| `data/users/*/claude-data/sessions/*.meta.json` | **已废弃** | 旧版 session title 存储，已在 sessions 表中 |

可通过 `scripts/cleanup_stale_files.py --confirm` 清理。

---

## 9. 可借鉴的改进方案

### 已完成

| # | 建议 | 完成说明 | 相关文件 |
|---|------|---------|---------|
| 1 | **统一数据回读路径** | `buffer.get_history()` 已增加 `_read_db_sync()` SQLite 回读层，内存未命中时优先读 SQLite。JSONL 文件不再被写入。 | `src/message_buffer.py:153-198,211-228` |
| 2 | **心跳失活检测** | 前端每 10s 检查心跳，session 为 `running` 且 60s 无心跳时自动触发 `sendRecover`。详见 `docs/im-style-message-recovery-plan.md`。 | `App.tsx:289-445` |
| 3 | **恢复索引修复** | `computeRecoverIndex` 改为 `max(index) + 1` 而非 `messages.length`，修复非连续索引导致的恢复错误。 | `lib/session-state.ts:65-75` |
| 4 | **Session 计时器持久化** | `sessionStartTimesRef` Map 存储每个 session 的开始时间，切换回 A 时计时器继续而非从 0 开始。 | `ChatArea.tsx:75-96` |
| 5 | **会话切换状态合并** | `mergeSessionStates` 合并 DB 状态与 live buffer 状态，优先选择更"活跃"的状态。 | `lib/session-state.ts:40-50` |
| 6 | **JSONL → SQLite 迁移** | `message_buffer.py` 移除 `_write_disk()` 调用，新增 `_write_db_sync()` 和 `_read_db_sync()`。JSONL 仅作为最终兼容兜底。 | `src/message_buffer.py:96-198` |
| 7 | **断线消息队列** | `pendingQueue` 在 `useWebSocket.ts` 中实现，断线期间的消息会排队，重连后通过 `flushPending()` 自动发送。 | `useWebSocket.ts:29,40-47,91-94` |
| 8 | **滚动位置持久化** | `localStorage` 存储滚动位置，页面刷新后恢复。 | `App.tsx:28-41`, `ChatArea.tsx:36-53` |

### 部分完成

| # | 建议 | 当前状态 | 剩余工作 | 相关文件 |
|---|------|---------|---------|---------|
| 1 | **游标化恢复** | `computeRecoverIndex` 已正确计算恢复索引（session 切换 + 心跳失活场景）。 | WebSocket 断线重连后仍从 `index 0` 全量恢复（`App.tsx:417`），需改为从持久化的 `last_known_index` 增量恢复。 | `App.tsx:414-423` |
| 2 | **心跳间隔优化** | 心跳失活检测已实现（60s 阈值）。 | 服务端心跳仍为 30s（`HEARTBEAT_INTERVAL = 30`），客户端检测间隔 10s。可考虑缩短到 15s 以更快检测断线。 | `src/message_buffer.py:23`, `main_server.py:1382` |
| 3 | **断线消息队列** | `pendingQueue` 已实现，断线期间的消息会排队，重连后通过 `flushPending()` 发送。 | 队列无容量上限，无超时淘汰机制；长时间断线后队列可能包含过时消息。`flushPending` 中 `type` 被硬编码为 `'chat'`（第 45 行），answer 类型消息发送时类型会被覆盖。 | `useWebSocket.ts:29,44-45,108` |

### 待实施

| # | 优先级 | 建议 | 说明 | 估计工作量 | 依赖 |
|---|--------|------|------|-----------|------|
| 1 | 高 | **发送失败反馈** | 当消息发送失败或超时时，用户看不到任何提示，乐观渲染永久停留。需添加发送状态机（pending → sent → failed）+ 红色标记 + 重试按钮 + 超时机制。 | 中 | 无 |
| 2 | 高 | **WebSocket 断线增量恢复** | 重连后从 `index 0` 全量恢复对长 session 不友好。需持久化 `last_known_index`（localStorage），重连后传入正确的恢复游标。 | 小 | 无 |
| 3 | 中 | **UUID 去重** | 当前用户消息按 `content` 内容去重（`App.tsx:364-367`），相同内容的两条消息会被误去重。应为每条乐观消息生成 `client_msg_id`（UUID），服务端回显时带上，前端按 UUID 匹配。 | 中 | 无 |
| 4 | 中 | **本地消息缓存** | 使用 IndexedDB 缓存消息，页面刷新后从本地恢复 + 增量同步，避免全量 REST 拉取。对长 session 体验提升显著。 | 大 | 无 |
| 5 | 中 | **连接状态指示器** | 当前仅有 `connected` 布尔值。需区分：已连接 / 重连中 / 重连失败 / 队列积压。在 Header 或 InputBar 中显示。 | 小 | 无 |
| 6 | 低 | **消息分页加载** | 当前无分页，一次性加载全部历史。对 Agent 场景目前合理，但若支持长会话（代码审查、大型项目生成），建议引入游标分页懒加载。 | 中 | 无 |
| 7 | 低 | **发送超时机制** | 30s 无服务端确认则标记发送失败。依赖 #1 的发送失败反馈基础设施。 | 小 | #1 |
| 8 | 低 | **恢复状态指示器** | 恢复过程中显示 "正在同步消息..." 指示器，让用户知道正在后台恢复数据。 | 小 | 无 |
| 9 | 低 | **重连失败通知** | 当前 5 次重连失败后静默停止（`useWebSocket.ts:27,31-38`），无任何用户通知。需显示 "连接已断开，请刷新页面" 提示。 | 小 | 无 |
| 10 | 低 | **Pending 消息清理** | `handleNewSession` 不清理旧 session 的 `pendingUserMsgsRef`（`App.tsx:508-514`），长时间使用后可能积累无效 pending。 | 小 | 无 |
| 11 | 低 | **心跳重置优化** | 切换 session 时心跳总是重置为 `Date.now()`（`App.tsx:294`），对仍在运行的 session 可能导致过早标记为失活。应改为 per-session 追踪。 | 小 | 无 |
| 12 | 中 | **React Key 稳定性** | `ChatArea.tsx:283` 使用 `${msg.index}-${i}` 作为 key，在消息重排序时可能导致不必要的 DOM 重渲染。应改为 `msg.uuid ?? msg.index` 或生成稳定 key。 | 小 | #3 (UUID) |

### 9.4 中期（架构级）

| # | 建议 | 说明 |
|---|------|------|
| 1 | **消息队列** | 引入消息队列（如 Redis streams）解耦 Agent 输出与前端推送，支持多实例水平扩展。 |
| 2 | **离线消息队列** | 用户离线期间的消息在服务端排队，上线后批量推送。当前架构中 WebSocket 断开即丢失未发送消息。 |
| 3 | **多端同步** | 当前单 WebSocket 接收所有 session 消息，但不支持多端同时登录。如需多设备同步，需引入消息漫游机制。 |

---

## 10. 当前实现已正确的方面

1. **单调递增 `index` 排序** — 与 IM 系统的 `msg_id` 模式一致
2. **乐观 UI 渲染** — 用户消息立即显示，与 IM 一致
3. **多层去重** — 针对不同消息来源的去重策略设计合理
4. **心跳驱动的 staleness 检测** — 与 IM 系统的 heartbeat 模式一致，已实现 60s 阈值自动恢复
5. **会话切换时的状态保持** — 滚动位置、消息、sessionState 独立维护
6. **WebSocket 断线自动重连** — 指数退避策略正确
7. **session 级别的消息过滤** — 单 WebSocket 接收多 session 消息后过滤
8. **两层混合存储** — 内存（实时）+ SQLite（持久化），写入和读取路径已统一
9. **恢复索引计算** — `computeRecoverIndex` 使用 `max(index) + 1`，正确处理非连续索引
10. **fork 端点纯 DB 路径** — session fork 通过 `session_store` 操作，不再依赖 JSONL 文件
11. **断线消息队列** — `pendingQueue` 在 `useWebSocket.ts` 中正确实现，断线消息会在重连后自动发送
12. **滚动位置持久化** — `localStorage` 存储滚动位置，页面刷新后恢复

---

## 11. 已完成的迁移和改进

### 11.1 JSONL → SQLite 迁移

**时间**：2026-04-21

**变更**：
- `message_buffer.py`：移除 `add_message()` 中的 `_write_disk()` 调用
- `message_buffer.py`：新增 `_read_db_sync()` 方法，从 SQLite 读取历史消息
- `message_buffer.py`：`get_history()` 内存未命中时优先读 SQLite，JSONL 仅作为最终兼容兜底
- `session_store.py`：删除死代码 `_write_disk_session()`
- `main_server.py`：`fork_session` 端点改为纯 DB 路径（`session_store.create_session` + 标题复制）
- `tests/`：移除 JSONL 持久化测试，新增 SQLite 回读fallback 测试

**影响**：
- WebSocket 恢复路径不再依赖 JSONL 文件
- REST API 和 WebSocket 历史查询路径统一（均读取 SQLite）
- 磁盘残留 JSONL 文件可通过 `scripts/cleanup_stale_files.py --confirm` 清理

### 11.2 IM 风格消息恢复

**时间**：2026-04-21

**变更**：
- `App.tsx`：新增 `lastHeartbeatRef` 追踪最后心跳时间
- `App.tsx`：每 10s 检查心跳失活，session 为 `running` 且 60s 无心跳时自动触发 `sendRecover`
- `App.tsx`：会话切换时重置心跳计时器
- `App.tsx`：`computeRecoverIndex` 修复为 `max(index) + 1`

**影响**：
- 解决了 "Agent 已完成但前端卡在工作状态" 的问题
- 恢复索引正确计算，非连续索引场景不再导致消息丢失

### 11.3 Session 计时器持久化

**时间**：2026-04-21

**变更**：
- `ChatArea.tsx`：新增 `sessionStartTimesRef` Map 存储每个 session 的开始时间
- `ChatArea.tsx`：会话切换时恢复原始计时起点
- `ChatArea.tsx`：测试覆盖：4 个新测试用例验证计时器行为

**影响**：
- A 运行 → 切换到 B → 切换回 A 时，计时器继续而非从 0 开始

---

## 12. 具体实施计划

本节列出前 5 个最高优先级项目的具体实施步骤。每个项目包含：目标、涉及文件、实施步骤、测试策略和验收标准。

### 12.1 项目 1：发送失败反馈

**目标**：当消息发送失败或超时时，用户能看到明确的视觉反馈，并能一键重试。

**涉及文件**：
- `frontend/src/lib/types.ts` — 新增 `MessageSendState` 类型
- `frontend/src/hooks/useWebSocket.ts` — 改造 `sendMessage` 返回发送状态
- `frontend/src/App.tsx` — 集成发送状态到消息管理
- `frontend/src/components/MessageBubble.tsx` — 显示发送状态 UI

**实施步骤**：

**步骤 1：定义发送状态类型**（`frontend/src/lib/types.ts`）
- 新增 `export type MessageSendState = 'pending' | 'sending' | 'sent' | 'failed' | 'timeout'`
- 在 `Message` 接口中新增可选字段 `sendState?: MessageSendState`

**步骤 2：改造 useWebSocket hook**（`frontend/src/hooks/useWebSocket.ts`）
- 新增 `sendTimeout` 配置项（默认 30s）
- `sendMessage` 改为返回 `{ messageId: string, promise: Promise<void> }`，其中 `messageId` 为本次发送的唯一标识
- 内部维护 `Map<string, { timer, resolve, reject }>` 跟踪未确认的发送
- 在 `ws.onmessage` 中，当收到服务端回显的用户消息时，解析并 resolve 对应的 pending promise
- 超时后 reject promise，标记为 `timeout`
- `ws.onerror` 或 `ws.onclose` 时，将所有 pending 标记为 `failed`
- 新增 `getSendState(messageId: string): MessageSendState` 方法供外部查询

**步骤 3：App.tsx 集成发送状态**（`frontend/src/App.tsx`）
- 在 `handleSend` 中为每条乐观消息生成 `clientMsgId`（UUID v4）
- 发送时将 `clientMsgId` 附加到消息 payload
- 维护 `sendStateMap: Ref<Map<string, MessageSendState>>`
- 监听 `sendMessage` 返回的 promise，更新对应消息的 `sendState`
- 新增 `handleRetrySend(message: Message)` 回调

**步骤 4：MessageBubble 显示发送状态**（`frontend/src/components/MessageBubble.tsx`）
- 用户消息气泡右下角显示小图标：
  - `sending`：旋转的发送中图标
  - `failed`/`timeout`：红色感叹号 + hover 显示重试按钮
  - `sent`：无标记（或灰色对勾，可选）
- 重试按钮点击触发 `onRetry` 回调

**测试策略**：
- 单元测试：`useWebSocket.test.ts` — 测试发送超时、重连时 pending 标记失败、回显确认
- 组件测试：`MessageBubble.test.tsx` — 测试各发送状态的 UI 渲染
- 集成测试：模拟 WebSocket 断开场景，验证消息标记为 failed

**验收标准**：
- [ ] 消息发送 30s 无确认自动标记为 timeout，显示红色感叹号
- [ ] WebSocket 断开时所有 pending 消息标记为 failed
- [ ] 点击重试按钮重新发送消息
- [ ] 发送成功时图标消失
- [ ] 所有测试通过，覆盖率 80%+

---

### 12.2 项目 2：UUID 去重

**目标**：解决相同内容用户消息被误去重的问题，使用 `client_msg_id`（UUID）区分每次发送。

**涉及文件**：
- `frontend/src/lib/types.ts` — 扩展 `Message` 接口
- `frontend/src/App.tsx` — 生成 UUID，改造去重逻辑
- `frontend/src/hooks/useWebSocket.ts` — 传递 `client_msg_id`
- `main_server.py` — 回显时携带 `client_msg_id`

**实施步骤**：

**步骤 1：扩展 Message 类型**（`frontend/src/lib/types.ts`）
- 新增 `clientMsgId?: string` 字段到 `Message` 接口

**步骤 2：前端生成 UUID**（`frontend/src/App.tsx`）
- 在 `handleSend` 中为每条乐观消息生成 `clientMsgId = crypto.randomUUID()`
- 发送时将 `client_msg_id` 附加到 WebSocket payload

**步骤 3：改造去重逻辑**（`frontend/src/App.tsx`，约第 364 行）
- 将策略 3（用户消息按内容去重）改为：
  ```typescript
  // 按 clientMsgId 匹配（精确匹配每次发送）
  if (msg.type === 'user' && !msg.replay && msg.clientMsgId) {
    if (prev.some((m) => m.clientMsgId === msg.clientMsgId)) {
      return prev
    }
  }
  ```
- 保留内容匹配作为 fallback（兼容旧消息无 UUID 的情况）

**步骤 4：后端回显携带 UUID**（`main_server.py`）
- 在 WebSocket `chat` 消息处理中，提取 `data.get("client_msg_id")`
- 在回显用户消息时，将 `client_msg_id` 附加到消息 payload
- 修改位置：`main_server.py` 中 WebSocket chat handler（约第 1280-1340 行区域）

**测试策略**：
- 单元测试：发送两条内容相同但 UUID 不同的消息，验证都不会被去重
- 单元测试：发送同一条消息重试（相同 UUID），验证被正确去重
- 集成测试：模拟服务端回显带 UUID，验证前端去重

**验收标准**：
- [ ] 用户连续发送两条相同内容消息，两条都显示
- [ ] 服务端回显带 UUID 的消息被正确去重
- [ ] 旧消息（无 UUID）仍按内容去重（向后兼容）
- [ ] 所有测试通过，覆盖率 80%+

---

### 12.3 项目 3：WebSocket 断线增量恢复

**目标**：WebSocket 断线重连后，从 `last_known_index` 增量恢复而非从 `index 0` 全量恢复。

**涉及文件**：
- `frontend/src/hooks/useWebSocket.ts` — 重连时携带正确的 `last_index`
- `frontend/src/App.tsx` — 持久化和读取 `last_known_index`
- `frontend/src/lib/session-state.ts` — 新增持久化索引工具函数

**实施步骤**：

**步骤 1：新增持久化索引工具函数**（`frontend/src/lib/session-state.ts`）
- 新增 `saveLastKnownIndex(sessionId: string, index: number): void` — 写入 localStorage
- 新增 `loadLastKnownIndex(sessionId: string): number` — 读取 localStorage，默认 0
- 新增 `clearLastKnownIndex(sessionId: string): void` — 清理
- localStorage key 格式：`web-agent-last-index:{userId}:{sessionId}`

**步骤 2：在消息到达时更新索引**（`frontend/src/App.tsx`）
- 在 `handleIncomingMessage` 中，每当收到非心跳、非 system 的消息时，更新 `last_known_index`
- 使用 `useRef` 维护 per-session 的最大 index，批量写入 localStorage（避免每次消息都写）
- 在会话切换和组件卸载时 flush 当前 session 的 last index

**步骤 3：重连时使用增量恢复**（`frontend/src/hooks/useWebSocket.ts`）
- 改造 `connect` 回调，接收 `lastKnownIndex` 参数
- 在 `ws.onopen` 中，不再由 `useEffect` 层触发 `sendRecover(..., 0)`，而是在 hook 内部直接调用 `sendRecover(sessionId, lastKnownIndex)`
- 需要 hook 知道当前 active session ID，可通过新增 `activeSessionId` 参数传入

**步骤 4：修改 App.tsx 的自动恢复逻辑**（`frontend/src/App.tsx`，约第 414-423 行）
- 当前逻辑：
  ```typescript
  if (connected && activeSessionRef.current && !didRecoverRef.current) {
    didRecoverRef.current = true
    sendRecover(activeSessionRef.current, 0)
  }
  ```
- 改为：
  ```typescript
  if (connected && activeSessionRef.current && !didRecoverRef.current) {
    didRecoverRef.current = true
    const lastIndex = loadLastKnownIndex(activeSessionRef.current)
    sendRecover(activeSessionRef.current, lastIndex)
  }
  ```

**步骤 5：清理旧 session 的索引**（`frontend/src/App.tsx`）
- 在 `handleNewSession` 和 `handleDeleteSession` 中调用 `clearLastKnownIndex(id)`

**测试策略**：
- 单元测试：`session-state.test.ts` — 测试 localStorage 读写
- 集成测试：模拟 WS 断线重连，验证恢复索引为上次已知值而非 0
- E2E 测试：发送 100 条消息，刷新页面，断线重连，验证只恢复增量消息

**验收标准**：
- [ ] 页面刷新后重连，从上次已知 index 恢复
- [ ] 切换 session 后重连，使用新 session 的 last index
- [ ] 删除 session 后清理对应索引
- [ ] 所有测试通过，覆盖率 80%+

---

### 12.4 项目 4：本地消息缓存

**目标**：使用 IndexedDB 缓存消息，页面刷新后先从本地加载，再增量同步，显著加快加载速度。

**涉及文件**：
- `frontend/src/lib/message-cache.ts` — 新增 IndexedDB 缓存层（新文件）
- `frontend/src/App.tsx` — 集成缓存加载和同步逻辑
- `frontend/src/lib/types.ts` — 扩展 Message 类型（如需要）

**实施步骤**：

**步骤 1：创建 IndexedDB 缓存模块**（`frontend/src/lib/message-cache.ts`，新文件）
- 使用原生 IndexedDB API（或轻量封装如 `idb`）
- 数据库名：`web-agent-messages`，版本 1
- 对象存储：`messages`，keyPath 为 `[session_id, index]`（复合主键）
- 提供 API：
  - `cacheMessages(sessionId: string, messages: Message[]): Promise<void>` — 批量写入
  - `getMessages(sessionId: string): Promise<Message[]>` — 读取某 session 全部消息
  - `getMaxIndex(sessionId: string): Promise<number>` — 获取最大 index
  - `clearSession(sessionId: string): Promise<void>` — 清理某 session 缓存
  - `clearAll(): Promise<void>` — 清空所有缓存
- 设置容量上限（如每个 session 最多缓存 1000 条，总大小不超过 50MB）

**步骤 2：页面加载时从缓存恢复**（`frontend/src/App.tsx`，约第 209-257 行）
- 修改 `useEffect`（activeSession mount 时）：
  1. 先从 IndexedDB 加载缓存消息，立即显示
  2. 后台并行发起 REST 请求拉取最新历史
  3. 合并缓存与 REST 结果（以 REST 为准，用 index 去重）
  4. 触发 `sendRecover` 获取增量
- 用户体验：页面刷新后瞬间显示历史消息，后台静默同步

**步骤 3：新消息写入缓存**（`frontend/src/App.tsx`）
- 在 `handleIncomingMessage` 中，当收到非心跳消息时，异步写入 IndexedDB
- 使用 requestIdleCallback 或 debounce 避免频繁写入影响渲染性能

**步骤 4：会话切换时同步缓存**（`frontend/src/App.tsx`，`handleSelectSession`）
- 切换 session 时，先从 IndexedDB 加载缓存
- REST 请求返回后合并
- 更新缓存

**测试策略**：
- 单元测试：`message-cache.test.ts` — 测试 IndexedDB CRUD 操作
- 集成测试：模拟页面刷新，验证缓存加载 + REST 合并
- 性能测试：对比有缓存和无缓存的首屏加载时间

**验收标准**：
- [ ] 页面刷新后 200ms 内显示缓存消息
- [ ] REST 同步后消息完整无遗漏
- [ ] 长 session（500+ 消息）缓存写入不阻塞 UI
- [ ] IndexedDB 满或不可用时优雅降级为纯 REST
- [ ] 所有测试通过，覆盖率 80%+

---

### 12.5 项目 5：连接状态与重连指示

**目标**：在 UI 中清晰展示 WebSocket 连接状态，区分已连接 / 重连中 / 重连失败，并在重连失败时提供明确指引。

**涉及文件**：
- `frontend/src/hooks/useWebSocket.ts` — 暴露 `connectionStatus` 枚举
- `frontend/src/components/Header.tsx` 或 `InputBar.tsx` — 显示连接状态
- `frontend/src/App.tsx` — 传递状态到 UI 组件

**实施步骤**：

**步骤 1：定义连接状态枚举**（`frontend/src/hooks/useWebSocket.ts`）
- 新增 `export type ConnectionStatus = 'connected' | 'connecting' | 'reconnecting' | 'failed'`
- 替换现有 `connected: boolean` 为 `status: ConnectionStatus`
- 在以下时机更新状态：
  - `ws.onopen` → `connected`
  - `ws.onclose` 且 `reconnectAttempts < maxAttempts` → `reconnecting`
  - `ws.onclose` 且 `reconnectAttempts >= maxAttempts` → `failed`
  - `connect()` 调用但 WS 尚未 open → `connecting`
- 保持向后兼容：同时导出 `connected = status === 'connected'`

**步骤 2：暴露队列积压信息**（`frontend/src/hooks/useWebSocket.ts`）
- 新增 `pendingQueueSize: number` 到返回值
- 在 `flushPending` 后更新

**步骤 3：UI 显示连接状态**（`frontend/src/components/Header.tsx` 或 `InputBar.tsx`）
- 在 Header 的连接指示器旁添加状态文字：
  - `connected`：绿色圆点 + "已连接"
  - `reconnecting`：黄色旋转图标 + "重连中 (第 N 次)"
  - `failed`：红色圆点 + "连接已断开" + "刷新页面" 按钮
  - `connecting`：灰色旋转图标 + "连接中..."
- 当 `pendingQueueSize > 0` 时显示 "N 条消息排队中"

**步骤 4：禁用输入**（`frontend/src/App.tsx`，约第 741 行）
- 当前 `disabled={!connected || activeSessionState === 'running'}`
- 改为 `disabled={status !== 'connected' || activeSessionState === 'running'}`
- 在 `failed` 状态下显示横幅提示 "连接已断开，请刷新页面重试"

**测试策略**：
- 单元测试：`useWebSocket.test.ts` — 测试各状态转换
- 组件测试：验证各连接状态下的 UI 渲染
- E2E 测试：模拟网络断开，验证重连指示和最终失败提示

**验收标准**：
- [ ] 连接正常时显示绿色指示
- [ ] 断线后显示黄色重连中 + 次数
- [ ] 5 次重连失败后显示红色 + 刷新按钮
- [ ] InputBar 在重连和失败状态下正确禁用
- [ ] 所有测试通过，覆盖率 80%+

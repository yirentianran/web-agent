# 对话流逻辑梳理与问题分析

> 分析日期: 2026-05-02
> 分支: dev_20260428
> 分析范围: WebSocket 通信、消息收发、状态管理、断线重连、页面刷新恢复

---

## 一、整体架构概览

```
┌─────────────────────────────────────────────────────────────────────┐
│  Frontend (React + Vite)                                            │
│  ┌──────────┐  ┌──────────────┐  ┌─────────────┐  ┌──────────────┐ │
│  │ App.tsx  │  │ useWebSocket │  │ ChatArea.tsx │  │ StatusSpinner│ │
│  │ 状态管理  │──│ WS连接/重连   │  │ 消息渲染     │  │ 运行中动画    │ │
│  │ 消息去重  │  │ 队列/超时     │  │ 滚动控制     │  │ 耗时计时      │ │
│  └──────────┘  └──────────────┘  └─────────────┘  └──────────────┘ │
└───────────────────────────┬─────────────────────────────────────────┘
                            │ WebSocket (ws://)
                            │ REST API
┌───────────────────────────┴─────────────────────────────────────────┐
│  Backend (FastAPI)                                                  │
│  ┌───────────────┐  ┌──────────────────┐  ┌──────────────────────┐ │
│  │ handle_ws()   │  │ run_agent_task() │  │ MessageBuffer        │ │
│  │ WS消息路由     │  │ ClaudeSDKClient   │  │ 内存缓存 + SQLite   │ │
│  │ 订阅循环       │  │ 工具权限/TODO     │  │ 消费者事件通知       │ │
│  └───────────────┘  └──────────────────┘  └──────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### 关键数据流

1. **发送消息**: InputBar → handleSend → (optimistic insert) → WebSocket send → 后端 handle_ws → run_agent_task → ClaudeSDKClient → buffer.add_message → subscribe loop → WebSocket push
2. **接收消息**: WebSocket onmessage → handleIncomingMessage → 去重/状态更新/sendState 同步 → setMessages → ChatArea 渲染
3. **恢复消息**: WebSocket connect → sendRecover → 后端 recover 通道 → 只读订阅循环 → 逐条推送后续消息

---

## 二、前端逻辑详解

### 2.1 WebSocket 连接管理 (`useWebSocket.ts`)

**连接生命周期**:
- 组件挂载时创建 WebSocket，URL 为 `ws://${host}/ws?token=xxx`
- 连接成功 → `onopen` 重置重试次数，`flushPending()` 排空队列
- 连接断开 → `onclose` 指数退避重连（`min(1000 * 2^n, 10000)ms`），最多 5 次
- 5 次失败 → 状态设为 `"failed"`，清除所有 pending sends

**消息队列**:
- `pendingQueue`（max 100）: 离线时缓冲 chat 消息
- `priorityQueue`（max 10）: 离线时缓冲 answer 消息（AskUserQuestion 应答），优先发送
- 每条 chat 消息设置 30 秒超时，超时触发 `onSendFailed`

**StrictMode 安全**:
- 每个 WebSocket 实例有自己闭包内的 `intentionalClose` 标志
- cleanup 函数只关闭自己创建的 WS，不误清全局 pending sends

### 2.2 消息处理 (`App.tsx` — `handleIncomingMessage`)

**消息处理管道**（按顺序）:

1. **Snake/camel 字段标准化**: `client_msg_id` → `clientMsgId`
2. **心跳处理**: 更新 `lastHeartbeatRef`；若 `agent_alive === false` 则触发即时恢复
3. **流式文本聚合**: `content_block_delta` 事件累积到 `streamingTextState`
4. **last_known_index 持久化**: 非心跳/系统消息写入 localStorage
5. **用户消息索引跟踪**: 更新 `highestUserMsgIndexRef`
6. **发送确认**: 后端回显用户消息 → 清除 pending + 更新 sendState 为 "sent"
7. **跨会话过滤**: 非活跃会话消息只更新状态，不显示
8. **去重插入**: 按 `clientMsgId` > 内容 > `index` 优先级去重
9. **sendState 恢复**: 每次 setMessages 后从 `sendStateMapRef` 重新应用真实状态
10. **状态变更**: `session_state_changed` → 更新 sessionStates map
11. **自动激活**: 首个消息到达且无活跃会话时自动设置 activeSession

### 2.3 状态机 (`session-state.ts`)

```
状态排序: idle(0) < completed(1) < running(2) = waiting_user(2) < error(3) = cancelled(3)

转换:
  idle ──send──→ running ──result──→ completed
                  │    │
                  │    └──error──→ error
                  │
                  └──cancel──→ cancelled
                  │
                  └──AskUserQuestion──→ waiting_user ──answer──→ running
```

**状态合并策略**: `mergeSessionStates(bufferState, dbState)` 取 activity level 更高者，防止数据库的旧 `idle` 覆盖内存中的 `running`。

**last_known_index 管理**:
- 每次非系统消息到达时写入 localStorage
- 会话切换时保存旧会话索引，加载新会话索引
- 页面刷新后通过 `loadLastKnownIndex` 恢复，传给 `sendRecover`

### 2.4 Spinner 逻辑 (`StatusSpinner.tsx` + `ChatArea.tsx`)

**显示条件**: `sessionState === "running"` 且 `sessionId !== null`

**计时逻辑**:
- `agentStartTime` 存储在 `sessionStartTimesRef` (per-session Map)
- 持久化到 localStorage (`web-agent-start-times`)，12 小时过期
- 从 terminal 状态过渡到 running → 删除旧时间、重新记录（新运行）
- 会话切换时保存当前计时、恢复目标会话计时
- `StatusSpinner` 用 `setInterval(1000)` 计算 `elapsed`

**staleness 检测**:
- 30 秒无活动 → CSS class `status-spinner--stale`（灰色显示）
- 60 秒无心跳 → 触发 `sendRecover` 尝试获取真实状态

**Spinner 入口点**:
| 场景 | 组件 | 条件 |
|------|------|------|
| Agent 运行中 | ChatArea | `sessionState === "running"` |
| 会话切换中 | ChatArea | `sessionLoading === true` |
| WS 重连中 | MainLayout | `status === "reconnecting"` (黄色横幅) |
| WS 断开 | MainLayout | `status === "failed"` (红色横幅) |
| 发送失败 | MessageBubble | `sendState === "failed"` (红色 X) |
| 发送中 | MessageBubble | `sendState === "sending"` (动画圆圈) |
| 队列满 | MainLayout | `queueFull === true` (黄色横幅) |

### 2.5 页面刷新逻辑

**刷新时的恢复流程**:

1. `userId`/`authToken` 从 localStorage 恢复
2. `loadSessions()` 加载会话列表
3. 如有 `activeSession`（从 localStorage），触发 history 加载 effect:
   - `GET /api/users/{userId}/sessions/{sessionId}/history` → 恢复消息列表
   - 从后往前扫描 history 推导 session state
   - `GET /api/users/{userId}/sessions/{sessionId}/status` → 获取实时 buffer 状态
   - 若 buffer 状态为 `running` 且新鲜（< 30s）→ 采用 `running`
   - 若 buffer 状态为 `running` 但过期（≥ 30s）→ 触发 `sendRecover`
4. WebSocket 连接建立 → `connected` effect 触发 `sendRecover`:
   - 从 localStorage 读取 `lastKnownIndex`
   - 若 messages 已有内容，从 lastKnownIndex 恢复；否则从 0 开始
5. Agent 开始时间从 localStorage 恢复，计时器接续

### 2.6 会话切换逻辑 (`handleSelectSession`)

1. 保存旧会话的 `last_known_index`
2. 清除旧会话的 pending 消息
3. `setSessionLoading(true)` → 显示切换 spinner
4. 恢复新会话的 pending 消息（若有）→ 立即显示
5. 加载 history → 推导状态 → 查询 buffer status
6. 合并状态（buffer vs DB），处理 stale running
7. 调用 `sendRecover` 同步实时消息
8. 标记 `didRecoverRef = true` 防止 auto-recover effect 重复发送

---

## 三、后端逻辑详解

### 3.1 WebSocket 消息处理 (`handle_ws`)

**消息分类**:
| 类型 | 处理方式 |
|------|---------|
| `chat` | 进入 outer loop → replay history → 创建/复用 agent task → subscribe loop |
| `recover` | 进入 recover loop（只读订阅，不创建 agent task）|
| `answer` | 解析 `pending_answers` future，解除 AskUserQuestion 阻塞 |

**subscribe loop 核心逻辑**:
1. 循环检查 WebSocket 新消息（非阻塞 `get_nowait`）
2. 有新消息: answer → 解析 future；其他会话 → 重新入队、退出循环；同会话 → 追加 buffer
3. `get_history(after_index=last_seen)` → 发送新消息
4. `is_done()` → 最后一次拉取 → `_emit_synthetic_state_change_if_missing` → 退出
5. `_wait_for_ws_or_buffer(event, queue, HEARTBEAT_INTERVAL)` 等待 30s
6. 超时 → 发送 heartbeat（含 `agent_alive` 标志）

**recover loop 核心逻辑**:
- 与 subscribe loop 结构相同，但不创建 agent task
- 收到同会话的新 chat 消息 → 重新入队，退出 recover，交 outer loop 创建任务
- heartbeat 的 `agent_alive` 从 `active_tasks` 和 `buffer.get_state` 推断

**断开清理**:
- reader task 取消
- 孤儿 agent task（WS 关闭时仍在运行）→ cancel → 设置 error 状态

### 3.2 Agent 任务执行 (`run_agent_task`)

1. 创建 `ClaudeSDKClient` + `build_sdk_options`
2. 首次消息: `connect()` + `query(prompt)`
3. 后续消息: `connect(prompt=prompt_stream())`（构造历史 prompt）
4. `receive_response()` 迭代 → `message_to_dicts()` 转换 → `buffer.add_message()`
5. 检测 Write tool → 收集 generated files
6. 扫描 workspace 新文件（outputs/ 递归 + 根目录 + 外部目录）
7. 发送 `file_result` → `session_state_changed: completed` → `result` → `mark_done`
8. 自动生成会话标题

**异常处理**:
- `TimeoutError` → error 状态
- `CancelledError` → cancelled 状态
- `Exception` → error 状态（JSON buffer overflow 特殊处理）

### 3.3 MessageBuffer (`message_buffer.py`)

**双写策略**:
- 内存 `sessions` dict: 实时 push（`add_message` → 唤醒 consumers）
- SQLite: 异步 drain loop 写入，失败时存入 `unpersisted_messages` 重试

**消息索引**:
- `base_index`: 旧消息淘汰时的偏移补偿
- `seq`: 每会话独立递增，用于 DB 排序
- `get_history(after_index)`: 内存有 → 直接用；内存不够 → 回退 SQLite

**消费者模式**:
- `subscribe()` → 创建 `asyncio.Event`，加入 session 的 consumers 集合
- `add_message()` → `event.set()` 唤醒所有等待的 consumers
- `_wait_for_ws_or_buffer()` → 同时等待 event 或 ws_queue，先到先处理

---

## 四、已发现问题

### 问题 1 [严重] WebSocket URL 硬编码 `ws://` 导致 HTTPS 部署不可用

**文件**: `frontend/src/hooks/useWebSocket.ts:130`

```typescript
const ws = new WebSocket(`ws://${window.location.host}${wsPath}`);
```

**问题**: HTTPS 页面上浏览器会阻止非安全 WebSocket 连接（mixed content），必须使用 `wss://`。

**修复建议**: 根据页面协议自动选择：

```typescript
const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
const ws = new WebSocket(`${protocol}//${window.location.host}${wsPath}`);
```

---

### 问题 2 [严重] Chat 消息在 subscribe loop 期间到达时被静默丢弃

**文件**: `main_server.py:2238-2252`

**问题**: 当 subscribe loop 正在运行（Agent 正在处理），如果同一个会话收到新的 chat 消息，代码将其添加到 buffer 但既不断开循环也不创建新任务。该消息只是留在了 buffer 中，不会被任何 agent task 处理。

```python
# 当前代码 — line 2238-2252
else:
    # Message for same session — process it
    user_message = item.get("message", "")
    if user_message:
        logger.info("WS: new message for active session %s", session_id)
        buffer.add_message(session_id, {
            "type": "user",
            "content": user_message,
            ...
        })
```

**影响场景**: 用户快速连续发送两条消息。第一条创建 agent task 并进入 subscribe loop，第二条到达时被加入 buffer 但不会触发任何操作。用户看到第一条被处理，第二条一直没有响应，直到 WebSocket 断开重连后才可能被处理。

**修复建议**: 将新旧消息合并处理，或重新入队让 outer loop 创建新的 agent task：

```python
else:
    # Re-queue for outer loop — current task may be stale
    pending_ws_msgs.put_nowait(item)
    break  # exit subscribe loop, outer loop will create new task
```

但这会导致当前正在运行的 agent 结果被丢弃。更好的方案是等当前 task 完成后自动处理排队的消息，或在发送第二条消息时取消当前 task 并创建新 task。需根据产品需求决定。

---

### 问题 3 [严重] Recover loop 中 heartbeat `agent_alive` 检查可能循环触发恢复

**文件**: `main_server.py:2097-2101` + `frontend/src/App.tsx:567-569`

**后端 recover loop heartbeat**:
```python
task_key = f"task_{session_id}"
agent_alive = (
    (task_key in active_tasks and not active_tasks[task_key].done())
    or buffer.get_state(session_id) == "running"
)
```

**问题**: recover loop 期间不创建 agent task，所以 `task_key not in active_tasks` 为 True。如果 buffer 状态也不是 `"running"`，heartbeat 的 `agent_alive = false`。

**前端收到 `agent_alive: false` 的心跳**:
```typescript
if (msg.agent_alive === false && activeSessionRef.current) {
    sendRecoverRef.current(activeSessionRef.current, messages.length);
}
```

**影响**: 当前端已经在 recover loop 中时又收到 `agent_alive: false` 的心跳，会触发第二次 `sendRecover`。这会在 pending_ws_msgs 中排入另一个 recover 请求，recover loop 顶部检查到 `item.get("type") == "recover"` 时 `continue` 跳过。不会造成死循环但会产生多余的 WS 消息。

**修复建议**: 在 recover loop 的 heartbeat 逻辑中，如果 session 状态是 terminal（completed/error/cancelled），设置 `agent_alive` 时保持 true（心跳只是连接保活，不应该触发恢复）：

```python
buf_state = buffer.get_state(session_id)
if buf_state in ("completed", "error", "cancelled"):
    agent_alive = True  # Don't trigger re-recovery for finished sessions
elif task_key in active_tasks and not active_tasks[task_key].done():
    agent_alive = True
else:
    agent_alive = buffer.get_state(session_id) == "running"
```

---

### 问题 4 [高] 会话切换时可能发送重复的 `sendRecover`

**文件**:
- `frontend/src/App.tsx:1249` (handleSelectSession 内)
- `frontend/src/App.tsx:915-930` (auto-recover useEffect)

**问题**: `handleSelectSession` 在第 1249 行调用 `sendRecover(id, computeRecoverIndex(msgs))`，同时在第 1250 行设置 `didRecoverRef.current = true`。但 auto-recover effect（915-930 行）依赖 `connected` 状态，如果 `connected` 从 `false` 变为 `true`（比如之前的会话切换导致了重连），它会额外发送一次 `sendRecover`。

`sidRecoverRef` 设为 true 后，在同一个 effect 执行周期内 auto-recover 被阻止。但如果 `connected` 状态在 `handleSelectSession` 的 `sendRecover` 之后才变为 `true`（异步），auto-recover effect 会再次触发。

**修复建议**: 将 `didRecoverRef` 的设置提前到 `handleSelectSession` 开始时，或在 auto-recover effect 中检查 session 切换是否已处理：

```typescript
useEffect(() => {
    if (connected && activeSessionRef.current && !didRecoverRef.current) {
        didRecoverRef.current = true;
        const lastIndex = loadLastKnownIndex(activeSessionRef.current, userId);
        sendRecover(activeSessionRef.current, messages.length > 0 ? lastIndex : 0);
    }
    if (!connected) {
        didRecoverRef.current = false;
    }
}, [connected, sendRecover]);
```

当前代码已经 `didRecoverRef = true` 在 1250 行，但 auto-recover effect 在 914 行创建。如果 `handleSelectSession` 的执行先于 auto-recover 的 effect 触发（它们在不同的调度周期），auto-recover 看到的 `didRecoverRef.current` 已经是 `true`，不会重复。但关键的竞态是：如果 user 快速切换两次会话，中间 `connected` 状态变化可能导致第二次切换时 auto-recover 先于 handleSelectSession 的 sendRecover 触发。需要更细致的跟踪。

---

### 问题 5 [高] `maxMsgIndexRef` 在 `handleSend` 中使用时可能已过期

**文件**: `frontend/src/App.tsx:1024`

```typescript
const lastBackendIndex = maxMsgIndexRef.current;
```

**问题**: `maxMsgIndexRef` 在 `useEffect` 中更新（398-413 行），该 effect 依赖 `[messages]`。当 WebSocket 推送新消息时：
1. `handleIncomingMessage` → `setMessages(prev => ...)` 更新 messages
2. React 调度重渲染
3. 用户在重渲染之前点击发送 → `handleSend` 读取 `maxMsgIndexRef.current`
4. 此时 `maxMsgIndexRef` 还是旧值（useEffect 尚未执行）

因此 `last_index` 参数会偏小，后端 `get_history(after_index=last_index)` 会重放更多旧消息。

**修复建议**: 在 `handleIncomingMessage` 的 setMessages 回调中直接更新 `maxMsgIndexRef`，确保它在每次消息变更后立即反映最新值：

```typescript
setMessages((prev) => {
    // ... dedup logic ...
    // Update maxMsgIndexRef synchronously
    let maxIdx = maxMsgIndexRef.current;
    for (const m of next) {
        if (m.index != null && m.index > maxIdx) maxIdx = m.index;
    }
    maxMsgIndexRef.current = maxIdx;
    return withStates;
});
```

---

### 问题 6 [中] `activeSession` 加载 effect 依赖 `authToken` 导致不必要的重新执行

**文件**: `frontend/src/App.tsx:506`

```typescript
}, [userId, authToken]);
```

**问题**: 该 effect 加载会话历史消息。当 `authToken` 在登录后被设置，effect 会触发，但此时可能 `activeSession` 为 null（首次登录），导致没有实际效果。如果 `authToken` 因 token 刷新而变化，effect 会重新执行并获取已加载过的历史。

**修复建议**: 将 `authToken` 从依赖中移除，改为在 effect 内部使用 ref 获取最新值：

```typescript
const authTokenRef = useRef(authToken);
authTokenRef.current = authToken;

useEffect(() => {
    // ... use authTokenRef.current ...
}, [userId, activeSession]); // 移除 authToken 依赖
```

---

### 问题 7 [中] `sendRecover` 缺少超时和重试机制

**文件**: `frontend/src/hooks/useWebSocket.ts:289-304`

**问题**: `sendMessage` 有 30 秒超时机制（`pendingSends` + `SEND_TIMEOUT_MS`），但 `sendRecover` 没有。如果 recovery 请求发出后后端没有响应（例如后端过载或崩溃），前端会永远等待，spinner 一直显示。

**修复建议**: 为 `sendRecover` 添加超时机制，超时后触发重试或降级处理：

```typescript
const sendRecover = useCallback(
    (sessionId: string, lastIndex: number) => {
        const ws = wsRef.current;
        if (ws?.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: "recover",
                session_id: sessionId,
                last_index: lastIndex,
                user_id: userIdRef.current,
            }));
            // Set a timeout: if no response within 15 seconds, retry or show warning
            const timer = setTimeout(() => {
                console.warn("[WebSocket] Recover timeout for session", sessionId);
                onSendFailedRef.current?.("recover:" + sessionId);
            }, 15_000);
            // Store timer so confirmSend can clear it when recovery messages arrive
        }
    },
    [],
);
```

---

### 问题 8 [中] `pendingUserMsgsRef` 在会话切换时被清除，可能导致消息丢失

**文件**: `frontend/src/App.tsx:1120`

```typescript
pendingUserMsgsRef.current.delete(oldSessionId);
```

**问题**: 用户发送消息后立即切换会话。此时消息已通过 WebSocket 发出但尚未收到后端回显确认（sendState 仍为 "sending"）。`handleSelectSession` 清除了该 pending 消息。当用户切回原会话时，pending 消息不再存在，history API 可能也没有这条消息（后端还在处理中），导致用户的发送看起来"丢失了"。

**当前设计**: 代码注释说 "pending messages are preserved so they survive the setMessages() replacement"，但实际上删除发生在第 1120 行。第 1137-1139 行尝试恢复 pending，但由于前面已删除，`pendingUserMsgsRef.current.get(id)` 返回 undefined。

**修复建议**: 不要清除旧会话的 pending 消息，保留它直到收到发送确认或超时：

```typescript
// 删除这一行:
// pendingUserMsgsRef.current.delete(oldSessionId);

// 改为在 sendState 变为 "sent" 或 "failed" 时才清除
```

---

### 问题 9 [中] 心跳 staleness 检测在 WebSocket 断开时无意义地触发 recovery

**文件**: `frontend/src/App.tsx:936-952`

```typescript
useEffect(() => {
    if (activeSessionState !== "running" || !activeSessionRef.current) return;
    const checkInterval = setInterval(() => {
        // ...
        const gap = Date.now() - lastHeartbeatRef.current;
        if (gap > 60_000) {
            lastHeartbeatRef.current = Date.now();
            sendRecover(sid, computeRecoverIndex(messages));
        }
    }, 10_000);
    return () => clearInterval(checkInterval);
}, [activeSessionState, messages, sendRecover]);
```

**问题**: 当 WebSocket 已断开连接且 `status` 为 `"reconnecting"` 或 `"failed"` 时，`lastHeartbeatRef` 不会更新，60 秒后必然触发 `sendRecover`。但此时 WebSocket 尚未连接，`sendRecover` 只会在控制台打印消息不入队（因为没有 pending queue for recover type），不产生实际效果。这是一种无效操作。

**修复建议**: 在检查中增加 WebSocket 连接状态判断：

```typescript
if (gap > 60_000 && status === "connected") {
    // Only attempt recovery when actually connected
    lastHeartbeatRef.current = Date.now();
    sendRecover(sid, computeRecoverIndex(messages));
}
```

---

### 问题 10 [低] 页面刷新时流式文本丢失

**文件**: `frontend/src/App.tsx:421-506`, `frontend/src/hooks/useStreamingText.ts`

**问题**: `content_block_delta` 累积的流式文本是纯内存状态，不持久化。如果用户在 Agent 正在输出长文本时刷新页面，已累积但尚未形成完整 assistant message 的文本会丢失。不过 assistant message 最终会完整出现在 history 中（由后端持久化），所以丢失的只是"正在流式输出的视觉体验"，不会丢失最终结果。这是一个 UX 问题而非数据丢失问题。

**当前处理**: 可接受。流式文本本质是 UI 加速显示，不是数据源。

---

### 问题 11 [低] Recover loop 结束后不检查 pending messages 队列

**文件**: `main_server.py:2111-2115`

```python
finally:
    buffer.unsubscribe(session_id, event)
    current_session_id = None

continue  # Back to outer loop
```

**问题**: 当 recover loop 退出时（收到不同会话的消息），该消息已经被 `put_nowait` 重新入队。但 outer loop 回到顶部后会先 `drain` 队列。如果在此期间又有新消息到达，处理顺序可能不是 FIFO。

**当前影响**: 低。队列中有多个消息时，处理顺序本身就是不确定的（谁先被 drain 到谁先处理）。目前的逻辑是可接受的。

---

## 五、问题严重度汇总

| # | 问题 | 严重度 | 影响范围 |
|---|------|--------|---------|
| 1 | WebSocket URL 硬编码 ws:// | **严重** | HTTPS 部署完全不可用 |
| 2 | Subscribe loop 中 chat 消息被丢弃 | **严重** | 快速连续发送时消息丢失 |
| 3 | Recover loop heartbeat 循环触发恢复 | **严重** | 不必要的 WS 消息和潜在循环 |
| 4 | 会话切换时重复 sendRecover | 高 | 重复消息、性能浪费 |
| 5 | maxMsgIndexRef 过期 | 高 | 错误的重放范围、可能消息乱序 |
| 6 | authToken 依赖导致 effect 重执行 | 中 | 不必要的网络请求 |
| 7 | sendRecover 无超时机制 | 中 | 恢复失败时 spinner 永远显示 |
| 8 | pendingUserMsgsRef 过早清除 | 中 | 快速切换会话时消息"消失" |
| 9 | 断连时无效 heartbeat staleness 检查 | 中 | 无效的恢复尝试 |
| 10 | 刷新时流式文本丢失 | 低 | 仅 UX，不影响数据完整性 |
| 11 | Recover loop 队列处理顺序 | 低 | 极端情况才有影响 |

---

## 六、建议修复优先级

1. **立即修复**: 问题 1（ws:// 硬编码） — 一行改动，影响所有 HTTPS 部署
2. **尽快修复**: 问题 2（消息丢弃）和问题 3（循环恢复） — 核心功能缺陷
3. **下个迭代**: 问题 4、5、7 — 状态同步可靠性
4. **技术债务**: 问题 6、8、9 — 代码质量和边界情况

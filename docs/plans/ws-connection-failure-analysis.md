# WebSocket 连接失败分析文档

## 问题描述

修改代码后，前端无法正常建立 WebSocket 连接。浏览器控制台报错：

```
WebSocket connection to 'ws://localhost:3000/ws?token=...' failed:
WebSocket is closed before the connection is established.
```

错误位于 `useWebSocket.ts:246`，即 `useEffect` 清理函数中的 `wsRef.current?.close()` 调用。

---

## 最近的代码变更

以下变更引入了问题（通过 `git diff` 分析）：

### 1. `useWebSocket.ts` 变更

| 变更 | 描述 |
|------|------|
| 新增 `queueFull` 状态 | 跟踪消息队列溢出 |
| 新增 `priorityQueue` | 优先级队列用于 `answer` 消息 |
| 新增 `onQueueFull` 回调 | 队列满时的通知回调 |
| `sendMessage` 队列满处理 | 从静默丢弃改为触发 `setQueueFull(true)` + `onQueueFull?.()` |
| `sendAnswer` 队列满处理 | 使用独立的 `priorityQueue` 替代 `pendingQueue` |

### 2. `App.tsx` 变更

| 变更 | 描述 |
|------|------|
| `handleDisconnect` 重构 | 不再重置 running 状态，改为留空 |
| 新增 `queueFull` 解构 | 从 `useWebSocket` 返回值获取 |
| 新增 `onQueueFull` 回调 | 打印队列溢出警告 |
| 新增 `queueFull` 横幅 | 页面顶部显示连接慢的提示 |
| 新增空 `if (connected)` 分支 | 无实际操作 |

---

## 根因分析

### 问题 1: `connect` useCallback 依赖过多导致频繁重建（关键）

**位置**: `useWebSocket.ts:134-141`

```ts
const connect = useCallback(() => {
  // ...
}, [
  userId,
  onMessage,       // ← 每次 App.tsx render 都会重建
  onConnect,       // ← undefined（未传递）
  onDisconnect,    // ← 每次 App.tsx render 都会重建
  scheduleReconnect, // 稳定（空依赖）
  flushPending,    // 依赖 userId，userId 不变时稳定
]);
```

**分析**:

`handleIncomingMessage`（作为 `onMessage` 传递）的依赖是 `[userId, updateSendState]`。如果 `updateSendState` 在每次 render 时重建（例如因为它依赖其他状态），那么 `onMessage` 就会在每次 render 时变化。

`handleDisconnect` 的依赖是 `[]`（空数组），所以它是稳定的。但 `onMessage` 的不稳定性会导致 `connect` 频繁重建。

**后果**: 每次 `connect` 重建都会触发 `useEffect` 清理 → `wsRef.current?.close()` → 关闭正在握手中的连接 → 重新创建新连接 → 新一轮循环。

### 问题 2: 缺少 `intentionalClose` 标志

**位置**: `useWebSocket.ts:243-250`

```ts
useEffect(() => {
  connect();
  return () => {
    wsRef.current?.close();  // ← 关闭后 onclose 会触发 scheduleReconnect
    if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    failPendingSends();
  };
}, [connect, failPendingSends]);
```

**分析**:

清理函数调用 `wsRef.current?.close()`，但关闭后 `ws.onclose` 回调会被触发，进而调用 `scheduleReconnect()`。虽然 `reconnectTimer` 被清除了，但状态已经被设置为 `"reconnecting"`。在 `connect` 频繁重建的场景下，这会导致连接状态混乱。

### 问题 3: `queueFull` 状态不会重置

**位置**: `useWebSocket.ts:51`

```ts
const [queueFull, setQueueFull] = useState(false);
```

**分析**:

一旦 `queueFull` 被设为 `true`，它永远不会被重置为 `false`。这意味着即使连接恢复，横幅警告也会一直显示。在 `App.tsx:703` 处有一个空的 `if (connected)` 分支，注释说 "no need to reset here"——但实际上**需要**在这里重置。

### 问题 4: `sendMessage` 依赖数组包含 `onMessage`

**位置**: `useWebSocket.ts:175`

```ts
[userId, onMessage, onQueueFull],
```

**分析**:

`sendMessage` 实际上并不使用 `onMessage`，但它被放在依赖数组中。这会导致 `sendMessage` 在 `onMessage` 变化时无意义地重建。

### 问题 5: 空 `if (connected)` 分支

**位置**: `App.tsx:703-705`

```ts
if (connected) {
  // queueFull is a state from useWebSocket, no need to reset here
}
```

**分析**:

这段代码无实际操作，是一个空操作。更重要的是，注释是错误的——实际上**需要**在这里重置 `queueFull` 状态。

---

## 影响范围

| 问题 | 严重程度 | 影响 |
|------|----------|------|
| `connect` 频繁重建 | 关键 | 连接永远无法建立，循环关闭/重建 |
| 缺少 `intentionalClose` | 高 | 清理时触发不必要的重连逻辑 |
| `queueFull` 不重置 | 高 | 警告横幅永久显示 |
| `sendMessage` 多余依赖 | 中 | 不必要的函数重建 |
| 空 `if` 分支 | 低 | 死代码，误导维护者 |

---

## 修复方案

### 修复 1: 使用 refs 存储回调（解决核心问题）

在 `useWebSocket` 内部使用 refs 存储 `onMessage`、`onConnect`、`onDisconnect`，使 `connect` 的依赖稳定：

```ts
// 新增
const onMessageRef = useRef(onMessage);
const onConnectRef = useRef(onConnect);
const onDisconnectRef = useRef(onDisconnect);

// 同步 refs（在每次 render 时更新）
useEffect(() => {
  onMessageRef.current = onMessage;
  onConnectRef.current = onConnect;
  onDisconnectRef.current = onDisconnect;
});
```

然后 `connect` 使用 `onMessageRef.current` 而不是 `onMessage`，依赖数组简化为 `[userId, scheduleReconnect, flushPending, token]`。

### 修复 2: 添加 `intentionalClose` 标志

```ts
const intentionalCloseRef = useRef(false);

// 在清理函数中
useEffect(() => {
  connect();
  return () => {
    intentionalCloseRef.current = true;
    wsRef.current?.close();
    if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    failPendingSends();
  };
}, [connect, failPendingSends]);

// 在 onclose 中检查
ws.onclose = () => {
  if (intentionalCloseRef.current) return;
  setStatus("reconnecting");
  onDisconnectRef.current?.();
  scheduleReconnect();
};
```

### 修复 3: 连接恢复时重置 `queueFull`

在 `ws.onopen` 中重置 `queueFull`：

```ts
ws.onopen = () => {
  reconnectAttempts.current = 0;
  setStatus("connected");
  setQueueFull(false);  // ← 新增
  onConnectRef.current?.();
  flushPending();
};
```

### 修复 4: 移除 `sendMessage` 的多余依赖

```ts
[userId, onQueueFull],  // 移除 onMessage
```

### 修复 5: 移除空 `if` 分支

删除 `App.tsx:703-705` 中的空分支。

---

## 验证计划

修复后需要验证：

1. **连接建立**: 页面加载后 WebSocket 正常连接，无 "closed before established" 错误
2. **消息发送**: 发送聊天消息正常接收和显示
3. **断开重连**: 刷新页面或网络断开后能自动重连
4. **AskUserQuestion**: 代理提问后用户能正常回答
5. **消息恢复**: 重连后消息历史正确恢复
6. **queueFull 横幅**: 队列溢出时显示，恢复后自动消失

---

## 相关文件

| 文件 | 需要修改 |
|------|----------|
| `frontend/src/hooks/useWebSocket.ts` | 是（主要修复） |
| `frontend/src/App.tsx` | 是（移除空分支） |

# Spinner / Loading Indicator 逻辑分析

## 架构概览

Spinner 由 ChatArea 中的 `sessionState === "running"` 控制（`ChatArea.tsx:239`），状态来自 `sessionStates.get(urlSessionId)`（`App.tsx:461-463`）。

**统一入口：** `setSessionStateFor(sessionId, newState)`（`App.tsx:443-458`）是所有会话状态变更的唯一入口，同时更新 React state 和 ref，避免 WebSocket 回调中的闭包过期问题。

### 状态转换触发点

| 触发点 | 位置 | 新状态 |
|--------|------|--------|
| `handleSend` | App.tsx:1375 | `running` |
| WebSocket `session_state_changed` | App.tsx:882-927, 1074-1086 | 后端返回值 |
| WebSocket `result` | App.tsx:1087-1094 | `completed` |
| `stopSession` (POST cancel) | App.tsx:1504 | `idle` |
| `handleSendFailed` | App.tsx:1131-1143 | `idle`（若当前为 running） |
| `onRecoverTimeout` | App.tsx:1159-1165 | `idle`（若为活跃会话） |
| REST `/history` + `/status` | App.tsx:630-678 | 从消息派生，保留活跃的 running |

### 安全网

| 机制 | 超时 | 效果 |
|------|------|------|
| 发送超时 | 5 分钟 | `handleSendFailed` → idle |
| 心跳陈旧检测 | 60s 无心跳 | 触发恢复 |
| 恢复超时 | 60s | `onRecoverTimeout` → idle |
| 陈旧检测间隔 | 每 10s | 检查心跳间隔 |

---

## 场景分析

### 场景 1：新发起会话

**流程：**
1. 用户导航至 `/chat/123`（新创建的会话）
2. REST 加载历史（空）和状态 → derivedState = `idle`
3. 用户输入消息并发送
4. `handleSend` → `setSessionStateFor(sessionId, "running")` → spinner 出现 ✅
5. 后端处理，agent 运行，流式返回结果
6. 完成时：WebSocket 发送 `result` 或 `session_state_changed: completed`
7. `handleIncomingMessage` 处理 → `setSessionStateFor(sessionId, "completed")` → spinner 消失 ✅

**安全网覆盖：**
- 若 WebSocket 未连接：发送超时（5 分钟）→ `handleSendFailed` 重置为 idle
- 若 agent 静默退出：心跳陈旧（60s）→ 触发恢复
- 若恢复无响应：恢复超时（60s）→ 重置为 idle

**结论：** ✅ 正确。

---

### 场景 2：老会话继续追问

**流程：**
1. 用户在已完成（completed/idle）的会话中输入追问
2. `handleSend` → `setSessionStateFor(sessionId, "running")` → spinner 出现 ✅
3. `agentStartTime` 过渡检测（ChatArea.tsx:157-166）：同一会话从非 running → running，重置计时器 ✅
4. 后续流程同场景 1

**agentStartTime 细节：**
```
// ChatArea.tsx:157-166
if (sessionState === "running" && prevSessionStateRef.current !== "running") {
  const now = Date.now();
  sessionStartTimesRef.current.set(sessionId, now);
  setAgentStartTime(now);
}
```
同一会话的 completed/idle → running 过渡正确触发计时器重置 ✅

**结论：** ✅ 正确。

---

### 场景 3：会话 A 执行中切换到会话 B

**流程：**
1. 会话 A 正在运行（spinner 显示中），用户点击侧边栏切换到会话 B
2. `urlSessionId` 从 A 变为 B
3. 会话 A 的状态保留为 `running` 在 `sessionStates` 中（切换时不清除）
4. 会话 B 的状态（idle/completed）生效 → ChatArea 的 `isAgentRunning` 变为 false → spinner 消失 ✅
5. **关键：** 会话 A 的状态仍通过 WebSocket 更新，即使它已不是活跃会话

**非活跃会话状态更新（App.tsx:847-866）：**
```
if (msg.session_id && msg.session_id !== urlSessionIdRef.current) {
  if (msg.type === "system" && msg.subtype === "session_state_changed") {
    // 索引过滤 + replay 检查后
    setSessionStateFor(msg.session_id, newState);  // ← 仍会更新非活跃会话
  }
  if (msg.type === "result") {
    setSessionStateFor(msg.session_id, "completed");  // ← 同样更新
  }
}
```

**潜在问题：** 心跳陈旧检测（App.tsx:1241-1261）仅对**活跃**会话运行：
```
if (activeSessionStatus !== "running" || !urlSessionIdRef.current) return;
```
如果用户在会话 A 运行期间切走，且此时 WebSocket 断开，A 的状态将永远保持 `running`。不过当用户切回 A 时，恢复机制会在挂载时修复。

**结论：** ✅ 基本正确。陈旧检测仅针对活跃会话的限制已通过切回时的状态恢复机制得到缓解。

---

### 场景 4：会话 A 运行中 → 切到 B → 切回 A

此场景有三个子情况：

#### 4a：A 在离开期间已完成

1. 用户在 B 时，WebSocket 发送 `session_state_changed: completed` 给 A
2. 非活跃会话分支（App.tsx:847-866）更新 → `sessionStates` 中 A = `completed`
3. 切回 A：spinner 不显示 ✅

#### 4b：A 仍在运行

1. `sessionStates` 中 A 仍为 `running`
2. 切回 A：ChatArea 的 `isAgentRunning` 为 true → spinner 立即出现 ✅
3. **agentStartTime 恢复**（ChatArea.tsx:125-149）：
   ```
   // 会话变更检测 → 提前返回分支
   if (agentSessionIdRef.current !== sessionId) {
     // sessionState === "running" && sessionId 存在
     const savedStart = sessionStartTimesRef.current.get(sessionId);
     setAgentStartTime(savedStart);  // ← 恢复原有计时器
     return;
   }
   ```
   计时器从上次离开的位置继续，不会重置为 0 ✅
4. REST 历史加载：行 647-648 规定若当前状态为 `running` 且派生状态不是 `running`，保留 `running` → spinner 不会闪烁 ✅
5. 状态端点检查：若 `buffer_age >= 30`，触发恢复 → 恢复回传当前状态

#### 4c：WebSocket 完成信号丢失

1. `sessionStates` 中 A 为 `running`（未收到完成信号）
2. 切回 A：spinner 出现
3. REST 历史中可能有完成记录，但行 647-648 保留 `running`
4. 状态端点：`buffer_age >= 30` → 触发恢复
5. 恢复回传 `completed` → spinner 消失 ✅
6. 即使恢复失败 → `onRecoverTimeout` → 重置为 `idle` ✅

**agentStartTime 清理细节：**
- 同一会话从 running → completed（正常完成）：删除 start time（ChatArea.tsx:170-178）✅
- 切换会话时（不同 sessionId）：提前返回，**不删除**原有 start time ✅
- 这意味着切换离开运行中的会话时，start time 被保留 ✅

**结论：** ✅ 正确。各边界情况均有妥善处理。

---

## 发现并修复的问题

### 1. 陈旧检测仅针对活跃会话 ✅ 已修复

`App.tsx:1241-1261` 的 `setInterval` 原来仅在 `activeSessionStatus === "running"` 时运行。已修改为检查 `sessionStatesRef` 中**所有** running 会话，每个 running 会话都会触发恢复。

**修复内容：**
- 遍历 `sessionStatesRef.current` 中所有状态为 `running` 的会话
- 活跃会话使用 `computeRecoverIndex(messages)` 计算恢复索引
- 非活跃会话使用 `loadLastKnownIndex(sid, userId) + 1` 计算恢复索引

### 2. `sessionStates` Map 无限增长 ✅ 已修复

`sessionStates` Map 原来从不清理旧条目。已在两个位置添加清理逻辑：

**修复内容：**
- `loadSessions`：加载会话列表后，删除 `sessionStates` 和 `sessionStatesRef` 中不存在于列表的条目
- `handleDeleteSession`：删除会话时同步清理 `sessionStatesRef.current`

---

## 总体评价

四种场景的 spinner 逻辑均正确。系统设计了多层安全网：
- 发送超时（5 分钟）
- 心跳陈旧检测（60 秒无心跳 → 恢复，覆盖所有 running 会话）
- 恢复超时（60 秒）
- REST 状态端点作为权威数据源
- 会话列表同步清理过期的 sessionStates 条目

agentStartTime 在会话切换时的保留/恢复处理尤其完善——计时器从离开位置继续，不会有"跳回 0"的问题。

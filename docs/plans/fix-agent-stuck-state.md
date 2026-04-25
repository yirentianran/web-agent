# Fix Plan: "Agent Is Working" Stuck State

## 问题分析

页面停留在 "Agent is working" 状态无法退出，根本原因是状态从 `running` 到 `completed`/`error` 的转换链路中存在 **5 个断点**：

### Bug 1：`mark_done()` 不唤醒消费者（30 秒延迟）

**文件**：`src/message_buffer.py:317-324`

`mark_done()` 设置 `done = True` 但不调用 `event.set()` 唤醒订阅循环。订阅循环只能等心跳超时间接检测。

```python
def mark_done(self, session_id: str) -> None:
    self._ensure_buf(session_id)["done"] = True  # sets done but doesn't wake consumers
    current_state = self.sessions[session_id].get("state", "idle")
    if current_state not in ("cancelled", "error"):
        self.sessions[session_id]["state"] = "completed"
    # MISSING: event.set() for consumer wakeup
```

**正常路径为什么有效**：因为在 `mark_done()` 之前有 `add_message()`（line 1261），`add_message()` 会调用 `event.set()`。但如果 `add_message()` 的事件被消费后、`mark_done()` 还没被检测到时，订阅循环就会重新进入 30 秒心跳等待。

**修复**：在 `mark_done()` 中唤醒消费者，消除最多 30 秒的检测延迟。

### Bug 2：心跳掩盖 Agent 死亡

**文件**：`main_server.py:1694-1709`，`frontend/src/App.tsx:661-677`

即使 Agent 任务崩溃退出，只要 WebSocket 连接和订阅循环存活，心跳（`make_heartbeat()`）就会每 30 秒发送一次。前端的陈旧检测阈值是 60 秒——心跳持续重置时钟，导致 **永远不触发恢复**。

```
Agent 崩溃 → subscribe loop 仍然存活 → 每 30s 发心跳
→ 前端 lastHeartbeatRef 每 30s 被重置
→ 60s gap 永远达不到 → 页面永远 "running"
```

**修复**：
- 后端：在心跳中加入 Agent 存活状态标记，或设置心跳最大发送次数
- 前端：改用 `last_active` 字段判断，如果 buffer 超过 120 秒无真实消息且 Agent 任务已不存在，强制转为 `error`

### Bug 3：恢复循环不发送状态转换消息

**文件**：`main_server.py:1460-1523`（recover loop）

当 `buffer.is_done(session_id) == True` 时，recover 循环会发送剩余消息然后退出。但如果 `session_state_changed` 消息的索引 **已经在之前的恢复中被发送过**，且 Agent 异常退出时没有添加 `session_state_changed` 消息，则 recover 循环退出但 **不发送任何状态转换消息**。

场景：
1. Agent 被外部进程杀死（SIGKILL）
2. `run_agent_task` 的 finally 块没机会执行
3. `mark_done()` 从未被调用 → `is_done()` 可能为 False
4. 即使有人调了 `mark_done()`，但 `session_state_changed` 消息可能不在 buffer 中

**修复**：在 recover 循环退出前，如果 buffer 状态是 `running` 且 `is_done() == True`，确保发送一条 `session_state_changed: completed` 消息。

### Bug 4：`highestUserMsgIndexRef` 守卫可能丢弃完成消息

**文件**：`frontend/src/App.tsx:504-518`, `App.tsx:583-588`

```typescript
// Line 504: invisible message path
if (msg.index != null && msg.index < highestUserMsgIndexRef.current) {
  // Skip — too old
}

// Line 584: visible message path
if (msg.index == null || msg.index >= highestUserMsgIndexRef.current) {
  setSessionStateFor(msg.session_id, newState);
}
```

`session_state_changed` 消息的 `index` 必须 `>= highestUserMsgIndexRef.current` 才能被接受。在正常情况下（agent 完成后消息序号一定大于用户消息序号）这不是问题，但如果：
- 消息排序异常（如 `result` 消息的 `add_message` 在 line 1271-1272 晚于 `session_state_changed` 在 line 1261-1268）
- `session_state_changed: completed` 的 index 可能被 `result` 消息的 index 影响

**修复**：对 `session_state_changed` 和 `result` 消息的索引守卫做更精细的判断——如果状态是终端状态（`completed`/`error`/`cancelled`），即使 index 略低也应接受（因为终端状态比 "running" 更可靠）。

### Bug 5：WebSocket 断开不重置会话状态

**文件**：`frontend/src/hooks/useWebSocket.ts:112-116`，`App.tsx:625-629`

```typescript
// useWebSocket.ts
ws.onclose = () => {
    setStatus("reconnecting");
    onDisconnect?.();  // Not wired up in App.tsx
    scheduleReconnect();
};

// App.tsx
} = useWebSocket({
    userId,
    onMessage: handleIncomingMessage,
    token: authToken ?? undefined,
    // onDisconnect is NOT passed → disconnect has no state effect
});
```

WebSocket 断开时，不会重置任何 `running` 状态的会话。重新连接后，如果 Agent 已经退出但前端状态仍是 `running`，需要恢复机制来修复。

**修复**：在 App.tsx 中传递 `onDisconnect` 回调，将所有 `running` 状态的会话转换为 `idle`（因为断开意味着 Agent 任务已脱离控制）。

### Bug 6：服务重启后 `_ensure_buf()` 不恢复终端状态（重启后永久 stuck）

**文件**：`src/message_buffer.py:54-91`（`_ensure_buf`）

重启后 `MessageBuffer.sessions` 为空。`_ensure_buf()` 只在 DB 最后一条消息是 `type == "result"` 时恢复 `done=True, state="completed"`：

```python
# _ensure_buf() line 78-88
cursor = self._sync_conn.execute(
    "SELECT type FROM messages WHERE session_id = ? ORDER BY seq DESC LIMIT 1",
    (session_id,),
)
row = cursor.fetchone()
if row and row[0] == "result":  # ONLY checks for "result"
    buf["done"] = True
    buf["state"] = "completed"
```

**如果会话在 DB 中的最后一条 `session_state_changed` 消息的 `state` 是 `"running"`**（agent 启动后写了 progress 消息，然后 agent 异常退出，`session_state_changed: completed/error` 没来得及写入 DB），`_ensure_buf()` 不会检测到任何终端状态——新 buffer 返回 `state: "idle"`。

**完整数据流（重启 stuck 场景）**：
```
1. 服务重启 → buffer.sessions = {}, active_tasks = {}
2. 用户打开会话 → handleSelectSession 加载 DB 历史
3. DB 历史包含 session_state_changed: running (崩溃前最后的状态)
4. 前端扫描历史 → derivedState = "running"
5. 前端获取 buffer 状态 → bufferState = "idle" (新 buffer)
6. mergeSessionStates("idle", "running") → "running" (running > idle)
7. 前端显示 "Agent is working"
8. 点击"停止" → cancel_session 没有 task 可取消，只设置 in-memory buffer
9. 刷新页面 → 重复步骤 2-7，永远 stuck
```

**修复**：在 `_ensure_buf()` 中，不仅检查最后一条消息类型，还要检查最后一条 `session_state_changed` 消息的 `state` 字段，如果为终端状态（`completed`/`error`/`cancelled`），直接恢复。

### Bug 7：`STATE_ORDER` 缺少 `cancelled` 状态（取消后刷新 stuck）

**文件**：`frontend/src/lib/session-state.ts:15-21`

```typescript
export const STATE_ORDER: Record<string, number> = {
  idle: 0,
  completed: 1,
  running: 2,
  waiting_user: 2,
  error: 3,
} as const
// NOTE: cancelled 不在 STATE_ORDER 中！
```

`cancelled` 不在 `STATE_ORDER` 中，导致 `mergeSessionStates()` 对其返回 `-1`：

```
mergeSessionStates("idle", "cancelled")
→ bufferOrder = STATE_ORDER["idle"] = 0
→ dbOrder = STATE_ORDER["cancelled"] ?? -1 = -1
→ bufferOrder(0) > dbOrder(-1) → 返回 "idle"
```

**完整数据流（取消 + 刷新 stuck 场景）**：
```
1. 用户点击"停止" → cancel_session → buffer.cancel(session_id)
   → in-memory state 设为 "cancelled"
2. 前端收到 ok → setSessionStateFor(activeSession, "idle")
3. 刷新页面 → handleSelectSession
4. DB 历史最后一条 session_state_changed: cancelled
   → derivedState = "cancelled"
5. buffer 是新的 → bufferState = "idle"
6. mergeSessionStates("idle", "cancelled") → "idle" (因为 cancelled = -1 < idle = 0)
7. 但如果 DB 历史中 cancelled 之前还有 running，而 cancelled 消息没被正确解析
   或因为其他原因 derivedState 回退到 "running" → 又回到 Bug 6 场景
```

**修复**：将 `cancelled` 加入 `STATE_ORDER`，优先级为 3（与 `error` 同级，终端状态）。

## 修复方案

### 修复 1：`mark_done()` 唤醒消费者

**文件**：`src/message_buffer.py`

在 `mark_done()` 方法末尾添加消费者唤醒逻辑：

```python
def mark_done(self, session_id: str) -> None:
    self._ensure_buf(session_id)["done"] = True
    current_state = self.sessions[session_id].get("state", "idle")
    if current_state not in ("cancelled", "error"):
        self.sessions[session_id]["state"] = "completed"
    # Wake up waiting consumers so subscribe loop detects completion immediately
    buf = self.sessions[session_id]
    for event in list(buf.get("consumers", set())):
        event.set()
```

**影响**：消除 30 秒心跳延迟检测，订阅循环在 `mark_done()` 后立即检测到完成。

### 修复 2：心跳中增加 Agent 存活标记

**文件**：`src/message_buffer.py`，`main_server.py`

在心跳消息中增加 `agent_alive` 标记。后端心跳循环检查 Agent 任务是否仍在运行：

```python
# In make_heartbeat():
def make_heartbeat(agent_alive: bool = True) -> dict:
    return {
        "type": "heartbeat",
        "timestamp": time.time(),
        "agent_alive": agent_alive,
    }
```

订阅循环在发送心跳前检查 `session_id` 是否在 `active_tasks` 中。如果不在，发送 `agent_alive: False` 的心跳。

**文件**：`frontend/src/App.tsx`

前端收到 `agent_alive: false` 心跳时，立即触发恢复：

```typescript
if (msg.type === "heartbeat" && msg.agent_alive === false) {
  // Agent task no longer exists on backend — trigger recovery
  sendRecover(activeSessionRef.current, computeRecoverIndex(messages));
}
```

### 修复 3：恢复循环确保发送终端状态

**文件**：`main_server.py`（recover loop at line 1460）

在 recover 循环的 `is_done()` 分支中，增加兜底状态消息发送：

```python
if buffer.is_done(session_id):
    # Final pull
    final_messages = buffer.get_history(session_id, after_index=last_seen)
    for i, h in enumerate(final_messages):
        idx = last_seen + i
        await websocket.send_text(json.dumps({**h, "index": idx, "replay": False, "session_id": session_id}))
    last_seen += len(final_messages)

    # Safety: if buffer state is terminal but no state_change was in the
    # final pull, emit one so the frontend can transition away from "running"
    buf_state = buffer.get_session_state(session_id)
    if buf_state["state"] in ("completed", "error", "cancelled"):
        # Check if we already sent a state_change in this pull
        has_state_change = any(
            m.get("type") == "system" and m.get("subtype") == "session_state_changed"
            for m in final_messages
        )
        if not has_state_change:
            await websocket.send_text(json.dumps({
                "type": "system",
                "subtype": "session_state_changed",
                "state": buf_state["state"],
                "index": last_seen,
                "replay": False,
                "session_id": session_id,
            }))
    break
```

同样的修复也应用于主订阅循环（line 1675）。

### 修复 4：放宽索引守卫对终端状态的过滤

**文件**：`frontend/src/App.tsx`

修改两个索引守卫，允许终端状态（`completed`/`error`/`cancelled`）绕过 `highestUserMsgIndexRef` 检查：

```typescript
const TERMINAL_STATES = new Set(["completed", "error", "cancelled"]);

// In the invisible message path (around line 497-519):
if (msg.type === "system" && msg.subtype === "session_state_changed" && msg.session_id) {
  const newState = msg.state || msg.content || "completed";
  const isTerminal = TERMINAL_STATES.has(newState);
  // Accept terminal state changes even if index is slightly lower
  // (agent completion should always be able to transition state)
  if (msg.index != null && msg.index < highestUserMsgIndexRef.current && !isTerminal) {
    // Skip
  } else if (msg.replay) {
    // ... existing replay logic
  } else {
    setSessionStateFor(msg.session_id, newState);
  }
}

// In the visible message path (around line 578-589):
if (msg.type === "system" && msg.subtype === "session_state_changed" && msg.session_id) {
  const newState = msg.state || msg.content || "completed";
  const isTerminal = TERMINAL_STATES.has(newState);
  if (msg.index == null || msg.index >= highestUserMsgIndexRef.current || isTerminal) {
    setSessionStateFor(msg.session_id, newState);
  }
}

// Also for result messages (around line 591-595):
if (msg.type === "result" && msg.session_id) {
  const isTerminal = true; // result is always terminal
  if (msg.index == null || msg.index >= highestUserMsgIndexRef.current || isTerminal) {
    setSessionStateFor(msg.session_id, "completed");
  }
  // ...
}
```

### 修复 5：WebSocket 断开重置 running 状态

**文件**：`frontend/src/App.tsx`

在 `useWebSocket` 调用中添加 `onDisconnect` 回调：

```typescript
} = useWebSocket({
    userId,
    onMessage: handleIncomingMessage,
    onDisconnect: () => {
      // Reset all running sessions to idle — the agent tasks are
      // no longer connected to this client
      for (const [sid, state] of sessionStatesRef.current) {
        if (state === "running" || state === "waiting_user") {
          setSessionStateFor(sid, "idle");
        }
      }
    },
    token: authToken ?? undefined,
});
```

### 修复 6：`_ensure_buf()` 恢复终端状态（服务重启 stuck）

**文件**：`src/message_buffer.py`

在 `_ensure_buf()` 中，不仅检查最后一条消息是否为 `result`，还检查最后一条 `session_state_changed` 消息的 `state` 字段：

```python
def _ensure_buf(self, session_id: str) -> dict[str, Any]:
    if session_id not in self.sessions:
        buf: dict[str, Any] = {
            "messages": [],
            "base_index": 0,
            "consumers": set(),
            "done": False,
            "state": "idle",
            "last_active": time.time(),
            "cost_usd": 0.0,
        }

        # On first access (e.g. after server restart), check if the
        # session had a terminal state in the database. This prevents
        # the recover loop from spinning forever on a completed session.
        if self.db is not None:
            if self._sync_conn is None:
                try:
                    self._sync_conn = sqlite3.connect(str(self.db.db_path))
                except Exception:
                    pass
            if self._sync_conn is not None:
                try:
                    # Check 1: if last message is "result" → completed
                    cursor = self._sync_conn.execute(
                        "SELECT type FROM messages WHERE session_id = ? "
                        "ORDER BY seq DESC LIMIT 1",
                        (session_id,),
                    )
                    row = cursor.fetchone()
                    if row and row[0] == "result":
                        buf["done"] = True
                        buf["state"] = "completed"
                    # Check 2: if last session_state_changed has terminal state
                    # (covers crash scenarios where result wasn't written)
                    elif row and row[0] == "system":
                        cursor2 = self._sync_conn.execute(
                            "SELECT payload FROM messages WHERE session_id = ? "
                            "AND type = 'system' AND subtype = 'session_state_changed' "
                            "ORDER BY seq DESC LIMIT 1",
                            (session_id,),
                        )
                        row2 = cursor2.fetchone()
                        if row2:
                            import json
                            payload = json.loads(row2[0])
                            terminal_state = payload.get("state")
                            if terminal_state in ("completed", "error", "cancelled"):
                                buf["done"] = True
                                buf["state"] = terminal_state
                except Exception:
                    pass  # DB unavailable — keep defaults

        self.sessions[session_id] = buf
    return self.sessions[session_id]
```

**影响**：服务重启后，即使 Agent 异常退出没有写入 `result` 消息，只要 DB 中有 `session_state_changed: completed/error/cancelled`，buffer 就能正确恢复终端状态。前端 `mergeSessionStates("completed", "running")` 会返回正确的终端状态。

### 修复 7：`STATE_ORDER` 加入 `cancelled` 状态

**文件**：`frontend/src/lib/session-state.ts`

```typescript
export const STATE_ORDER: Record<string, number> = {
  idle: 0,
  completed: 1,
  running: 2,
  waiting_user: 2,
  error: 3,
  cancelled: 3,  // Terminal state, same priority as error
} as const
```

**影响**：`mergeSessionStates("idle", "cancelled")` 现在返回 `"cancelled"`，取消后的会话刷新后能正确显示取消状态而不是回退到 `idle`。

## 实施顺序

| 步骤 | 修复 | 文件 | 优先级 | 测试 |
|------|------|------|--------|------|
| 1 | `_ensure_buf()` 恢复终端状态 | `src/message_buffer.py` | **P0** | 单元测试 |
| 2 | `mark_done()` 唤醒消费者 | `src/message_buffer.py` | P0 | 单元测试 |
| 3 | `STATE_ORDER` 加入 `cancelled` | `frontend/src/lib/session-state.ts` | **P0** | 单元测试 |
| 4 | 放宽索引守卫 | `frontend/src/App.tsx` | P0 | 组件测试 |
| 5 | 恢复循环发送终端状态 | `main_server.py` | P0 | 集成测试 |
| 6 | WebSocket 断开重置状态 | `frontend/src/App.tsx` | P1 | 组件测试 |
| 7 | 心跳中增加 Agent 存活标记 | 三端 | P2 | 集成测试 |

**说明**：步骤 1（`_ensure_buf` 修复）是重启 stuck 场景的根因修复，优先级最高。步骤 3（`cancelled` 加入 `STATE_ORDER`）是取消后刷新 stuck 的修复，同样是 P0。

## 测试计划

### 后端测试（`tests/unit/test_message_buffer.py`）

```python
def test_mark_done_wakes_consumers():
    """mark_done() must wake up waiting consumers immediately."""
    buf = MessageBuffer()
    buf.add_message("s1", {"type": "user", "content": "hello"})
    event = buf.subscribe("s1")

    buf.mark_done("s1")

    # Consumer should be woken
    assert event.is_set()


def test_ensure_buf_restores_cancelled_from_db(tmp_path):
    """After restart, buffer should restore 'cancelled' state from DB."""
    db_path = tmp_path / "test.db"
    session_id = "cancelled-session"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, "
        "seq INTEGER, type TEXT, subtype TEXT, content TEXT, payload TEXT, "
        "usage TEXT, created_at REAL DEFAULT 0)"
    )
    # Write a session that ended with cancelled
    conn.execute(
        "INSERT INTO messages (session_id, seq, type, subtype, payload, created_at) "
        "VALUES (?, 0, 'system', 'session_state_changed', "
        "'{\"state\": \"cancelled\"}', ?)",
        (session_id, time.time()),
    )
    conn.commit()
    conn.close()

    buf = MessageBuffer(
        base_dir=tmp_path / "buf",
        db=type("FakeDB", (), {"db_path": db_path})()
    )
    buf._sync_conn = sqlite3.connect(str(db_path))

    state = buf.get_session_state(session_id)
    assert state["state"] == "cancelled"
    assert buf.is_done(session_id) is True


def test_ensure_buf_restores_error_from_db(tmp_path):
    """After restart, buffer should restore 'error' state from DB."""
    db_path = tmp_path / "test.db"
    session_id = "error-session"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, "
        "seq INTEGER, type TEXT, subtype TEXT, content TEXT, payload TEXT, "
        "usage TEXT, created_at REAL DEFAULT 0)"
    )
    # Write a session that ended with error (no result message)
    conn.execute(
        "INSERT INTO messages (session_id, seq, type, content, created_at) "
        "VALUES (?, 0, 'user', 'hello', ?)",
        (session_id, time.time() - 100),
    )
    conn.execute(
        "INSERT INTO messages (session_id, seq, type, subtype, payload, created_at) "
        "VALUES (?, 1, 'system', 'session_state_changed', "
        "'{\"state\": \"error\"}', ?)",
        (session_id, time.time()),
    )
    conn.commit()
    conn.close()

    buf = MessageBuffer(
        base_dir=tmp_path / "buf",
        db=type("FakeDB", (), {"db_path": db_path})()
    )
    buf._sync_conn = sqlite3.connect(str(db_path))

    state = buf.get_session_state(session_id)
    assert state["state"] == "error"
    assert buf.is_done(session_id) is True
```

### 后端测试（`tests/unit/test_main_server.py`）

```python
async def test_recover_loop_emits_terminal_state_when_done():
    """Recover loop must emit session_state_changed if buffer is done
    but no state_change message exists in the final pull."""
    # ... test that recovering from a done buffer without a state_change
    #     message delivers a synthetic session_state_changed to the frontend
```

### 前端测试（`frontend/src/lib/session-state.test.ts`）

```typescript
test('mergeSessionStates prefers terminal over running', () => {
  expect(mergeSessionStates('running', 'completed')).toBe('completed')
  expect(mergeSessionStates('running', 'error')).toBe('error')
  expect(mergeSessionStates('running', 'cancelled')).toBe('cancelled')
})

test('mergeSessionStates handles cancelled correctly', () => {
  // cancelled should beat idle (it's a terminal state)
  expect(mergeSessionStates('idle', 'cancelled')).toBe('cancelled')
  // cancelled should beat completed (both terminal, but cancelled is more recent)
  expect(mergeSessionStates('cancelled', 'completed')).toBe('cancelled')
})
```

## 风险评估

| 风险 | 可能性 | 影响 | 缓解 |
|------|--------|------|------|
| `_ensure_buf` 恢复旧状态覆盖新状态 | 低 | 高 | 只在首次访问（重启后）生效，`done=True` 防止覆盖 |
| `mark_done()` 唤醒导致提前退出 | 低 | 中 | 唤醒前已确保 `done = True` 和 `state` 已设置 |
| 终端状态绕过索引守卫导致旧状态覆盖新状态 | 低 | 高 | 仅在 `completed`/`error`/`cancelled` 时绕过 |
| 断开时重置状态影响正常重连 | 中 | 低 | 仅在 `onclose` 触发，重连后恢复机制会重新加载正确状态 |

## 预期效果

修复完成后：
1. **服务重启** → 从 DB 恢复终端状态，会话在打开时即显示正确状态（`completed`/`error`/`cancelled`）
2. **Agent 正常完成** → 状态在 **<1 秒** 内从 `running` 变为 `completed`（消除 30 秒延迟）
3. **Agent 异常退出** → 前端在 **最多 30 秒** 内通过心跳+恢复机制转为 `error`/`completed`
4. **取消 + 刷新** → 正确显示 `cancelled` 状态（`cancelled` 在 `STATE_ORDER` 中）
5. **WebSocket 断开** → 所有 `running` 会话立即重置，重连后自动恢复
6. 任何场景下都不会出现永久卡在 `running` 的情况

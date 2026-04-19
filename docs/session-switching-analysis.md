# Session Switching Issues — Deep Analysis Report

> Date: 2026-04-19
> Scope: Session switching, message loss, stuck "Agent is working" status

---

## Issue 1: Rapid Session Switch Causes Message Loss

### Symptom
User sends a message, quickly switches to another session, then switches back. The original message is gone — neither displayed nor responded to.

### Root Cause Analysis

There are **three overlapping race conditions** that together cause this:

#### Race A: `handleSend` queues message on old session, then `handleSelectSession` sends `recover` for new session

**File**: `frontend/src/App.tsx`

When user clicks send on Session A:
1. `handleSend` (line 428) calls `sendMessage({ session_id: 'A', ... })`
2. The WebSocket message is sent, but the backend hasn't processed it yet
3. User immediately clicks Session B → `handleSelectSession('B')` fires (line 446)
4. `handleSelectSession` sends `sendRecover('B', msgs.length)` (line 488)

In the backend (`main_server.py`), the `ws_reader` coroutine queues both messages. The **outer loop** (line 1193) drains the queue and processes them in order. The issue is:

- If the `recover` for Session B is processed **before** the `chat` for Session A creates the agent task, the `recover` loop sets `current_session_id = 'B'` and enters the subscribe loop.
- When the `chat` message for Session A arrives in the queue (line 1332+), it creates the agent task and buffers the user message (line 1351) — **but no WebSocket subscribe loop is actively listening to Session A's buffer** because the outer loop is still inside Session B's recover loop.
- The `session_state_changed: running` message (line 1354-1358) is added to the buffer but **never streamed to the frontend** for Session A.
- When the user switches back to Session A, `handleSelectSession('A')` loads history from the DB — but the `session_state_changed: running` message may not have been flushed to SQLite yet, so the derived state is `'idle'`.

**Key gap**: The frontend optimistically sets `sessionStateFor('A', 'running')` at line 425, but when the user switches back, `handleSelectSession` **overwrites** this state with the DB-derived state (line 485), which is `'idle'`.

#### Race B: Frontend state is overwritten by stale DB data

**File**: `frontend/src/App.tsx`, lines 473-485

```typescript
// Derive sessionState from the last session_state_changed message
let derivedState = 'idle'
for (let i = msgs.length - 1; i >= 0; i--) {
  const m = msgs[i]
  if (m.type === 'system' && m.subtype === 'session_state_changed' && m.state) {
    derivedState = m.state
    break
  }
}
setSessionStateFor(id, derivedState)  // OVERWRITES optimistic state
```

This unconditionally overwrites any previously set state for the session. If the `session_state_changed: running` message was buffered in memory but not yet flushed to the DB, the history query returns no state-change message, and the state resets to `'idle'`.

#### Race C: `sendRecover` with wrong `last_index`

**File**: `frontend/src/App.tsx`, line 488

```typescript
sendRecover(id, msgs.length)
```

`msgs.length` is the count of messages returned from the DB history. But the backend buffer may have **more messages** that haven't been flushed to the DB yet. The recover should pass the index of the **last message the frontend has**, not the count of loaded messages.

If the user message was buffered but not in DB, `msgs.length` could be 0, and `sendRecover(id, 0)` would replay everything from index 0 — but this is correct in this case. The real issue is that the user message's `session_state_changed: running` was never sent over WebSocket (no active subscribe loop for that session when the message was added).

### Summary of Issue 1

| Component | Problem | Location |
|-----------|---------|----------|
| Frontend state | Optimistic `'running'` state overwritten by DB-derived `'idle'` | App.tsx:485 |
| Backend subscribe loop | No active listener for Session A when `running` state is set | main_server.py:1257-1330 |
| Message buffering | User message IS persisted, but state-change is lost in transit | main_server.py:1354-1358 |
| History state derivation | Ignores in-memory buffer state | App.tsx:473-485 + REST status poll timing |

---

## Issue 2: "Agent is working" Stuck Indefinitely

### Symptom
After the agent has completed its work, the UI continues to show "Agent is working..." spinner.

### Root Cause Analysis

There are **multiple potential causes**:

#### Cause A: `session_state_changed: completed` never reaches frontend

**File**: `main_server.py`, lines 1432-1445

The subscribe loop checks `buffer.is_done(session_id)` and does a final pull:

```python
if buffer.is_done(session_id):
    final_messages = buffer.get_history(session_id, after_index=last_seen)
    # ... send final_messages ...
    break
```

But `is_done()` can return `True` **before** the `session_state_changed: completed` message is added to the buffer. Here's the sequence in `run_agent_task` (lines 1096-1105):

```python
buffer.add_message(session_id, {  # line 1096 - state changed
    "type": "system",
    "subtype": "session_state_changed",
    "state": "completed",
})
if buffered_result is not None:  # line 1101
    buffer.add_message(session_id, buffered_result)
buffer.mark_done(session_id)  # line 1105 - sets done=True
```

The `mark_done()` call sets `done=True` AFTER the state-change message. So `is_done()` won't return `True` until after `mark_done()` is called — meaning the state-change message should already be in the buffer by the time `is_done()` returns `True`.

**However**, the issue is in the **subscribe loop timing**. The loop at line 1414 does:
1. `get_history(after_index=last_seen)` — gets new messages
2. `is_done(session_id)` — checks done flag

If the loop reads history (step 1) which includes the state-change message, but the `done` flag hasn't been set yet, the loop continues. Then `mark_done()` is called. On the next iteration, `get_history` returns nothing (already consumed), and `is_done()` returns `True` — so the loop breaks. The final pull at line 1436 should catch any remaining messages.

**This path seems correct.** The issue is likely elsewhere.

#### Cause B: Frontend derives `'running'` from status poll after `completed` state was set

**File**: `frontend/src/App.tsx`, lines 492-499

```typescript
fetch(`/api/users/${userId}/sessions/${id}/status`, { headers })
  .then(resp => resp.json())
  .then(status => {
    if (status.state === 'running') {
      setSessionStateFor(id, 'running')  // OVERWRITES completed state
    }
  })
```

This is a **race condition**: if the user switches to a session that was just completed, the DB might show `'completed'` in the history (line 485 sets state correctly), but the **status endpoint** might still return `'running'` if the buffer hasn't been flushed yet. The `.then()` callback then **overwrites** the correct `'completed'` state with `'running'`.

#### Cause C: Stale `running` state from non-active session

**File**: `frontend/src/App.tsx`, lines 286-293

```typescript
if (msg.session_id && msg.session_id !== activeSessionRef.current) {
  if (msg.type === 'system' && msg.subtype === 'session_state_changed') {
    setSessionStateFor(msg.session_id, msg.state || msg.content || 'completed')
  }
  // ...
  return
}
```

This correctly stores state for non-active sessions. But when the user later switches to that session, `handleSelectSession` re-derives state from history (line 485), which should be correct. The issue is if the history derivation misses the `completed` state (because it wasn't flushed to DB), it falls back to `'idle'` — **not** `'running'`. So this path alone shouldn't cause the stuck spinner.

#### Cause D: `heartbeat` messages keep state alive via StatusSpinner stale detection

**File**: `frontend/src/components/StatusSpinner.tsx`

The StatusSpinner tracks elapsed time and marks as "stale" after 30 seconds, but it **doesn't automatically hide** — it just changes the text. The spinner visibility is controlled by `isAgentRunning` in ChatArea (line 169: `sessionState === 'running'`). So the StatusSpinner itself isn't the cause of the stuck state, but it does make the stuck state visible.

### Summary of Issue 2

| Cause | Likelihood | Description |
|-------|-----------|-------------|
| A | Low | State-change message timing in subscribe loop — likely not the culprit |
| B | **HIGH** | Status poll race: `running` from poll overwrites `completed` from history |
| C | Medium | Non-active session state management — usually correct but fragile |
| D | Low | StatusSpinner display — symptom amplifier, not root cause |

---

## Issue 3: Additional Risks Found

### Risk 3A: Non-atomic agent task creation

**File**: `main_server.py`, lines 1333-1367

```python
task_key = f"task_{session_id}"
task_is_new = task_key not in active_tasks or active_tasks[task_key].done()
if task_is_new:
    # ... setup ...
    task = asyncio.create_task(run_agent_task(...))
    active_tasks[task_key] = task
```

The check (`task_is_new`) and the task creation (`asyncio.create_task`) are **not atomic**. Two rapid chat messages for the same session could both pass the check before either task is registered, creating **duplicate concurrent agent tasks** writing to the same buffer.

### Risk 3B: `recover` loop doesn't check for `chat` messages for different session

**File**: `main_server.py`, lines 1269-1272

```python
if item.get("session_id") and item.get("session_id") != session_id:
    pending_ws_msgs.put_nowait(item)
    break
```

This correctly breaks out of the recover loop when a message for a different session arrives. But if the new message is also a `recover` for a **third** session, the outer loop will process it, starting a new recover loop. The original session's recover is abandoned — which is fine because the user switched away. However, if the original session had a `chat` message queued, it could be delayed until the user switches back.

### Risk 3C: Frontend `clearThresholdRef` reset on session switch

**File**: `frontend/src/App.tsx`, lines 452-453

```typescript
clearThresholdRef.current = Number.MAX_SAFE_INTEGER
replayStartedRef.current = false
```

When switching sessions, these refs are reset. If a message was being sent on the old session and the recover completes first, the replay dedup logic may incorrectly clear messages or skip them.

---

## Recommended Fixes (Priority Order)

### Fix 1: Preserve in-memory state during session switch (HIGH priority)

**File**: `frontend/src/App.tsx`, `handleSelectSession`

After loading history from DB, also poll the backend status endpoint **before** setting the derived state, and prefer the more "active" state:

```typescript
// Before line 485, fetch status first
const statusResp = await fetch(`/api/users/${userId}/sessions/${id}/status`, { headers })
let bufferState = 'idle'
if (statusResp.ok) {
  const status = await statusResp.json()
  bufferState = status.state || 'idle'
}

// Derive state from history
let derivedState = 'idle'
// ... existing loop ...

// Prefer the more active state between DB history and live buffer
const stateOrder = { idle: 0, completed: 1, running: 2, waiting_user: 2, error: 3 }
const finalState = stateOrder[bufferState] > stateOrder[derivedState] ? bufferState : derivedState
setSessionStateFor(id, finalState)
```

Then **remove** the separate status poll at lines 492-499 (it's now merged and runs before state is set, avoiding the overwrite race).

### Fix 2: Ensure `session_state_changed` messages are always visible on session switch (HIGH priority)

**File**: `frontend/src/App.tsx`, `handleSelectSession`

After the status poll (Fix 1), if the buffer state is `'running'` or `'completed'` but the DB history doesn't contain the corresponding `session_state_changed` message, the recover call (line 488) will pick up the live messages from the buffer, which include the state-change message. The `handleIncomingMessage` callback will then process it and update the state correctly.

**Ensure** the recover is sent with the correct index — the index of the **last loaded message**, not the count:

```typescript
// Line 488: change from msgs.length to the actual last index
const lastIndex = msgs.length > 0 ? Math.max(...msgs.map(m => m.index)) + 1 : 0
sendRecover(id, lastIndex)
```

### Fix 3: Remove the delayed status poll (MEDIUM priority)

**File**: `frontend/src/App.tsx`, lines 492-499

Delete this block entirely. Fix 1 moves the status poll to run **before** state is set, eliminating the race where `running` overwrites `completed`.

### Fix 4: Atomic agent task creation (LOW priority, but important)

**File**: `main_server.py`, lines 1333-1367

Use an `asyncio.Lock` per session to make the check-and-create atomic:

```python
# At module level
_task_locks: dict[str, asyncio.Lock] = {}

# In handle_ws, around lines 1333-1367:
task_key = f"task_{session_id}"
if task_key not in _task_locks:
    _task_locks[task_key] = asyncio.Lock()

async with _task_locks[task_key]:
    task_is_new = task_key not in active_tasks or active_tasks[task_key].done()
    if task_is_new:
        # ... existing task creation code ...
```

---

## Architecture Recommendation (Long-term)

Consider a **per-session WebSocket subscription model** instead of the current single-WebSocket-multi-session approach. Each session would have its own logical subscription channel, and the frontend would simply subscribe/unsubscribe to channels without needing recover/replay logic. This eliminates most of the race conditions identified above.

Alternatively, keep the single WebSocket but move session state management to a **server-authoritative model**: the frontend never derives state from DB history; it always trusts the latest `session_state_changed` message from the buffer or status endpoint.

---

## Files Requiring Changes

| File | Changes | Risk |
|------|---------|------|
| `frontend/src/App.tsx` | Fix 1, 2, 3 — merge status poll, fix recover index, remove delayed poll | Medium |
| `main_server.py` | Fix 4 — atomic task creation | Low |
| `frontend/src/hooks/useWebSocket.ts` | No changes needed | — |

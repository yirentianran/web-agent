# Fix: "Agent Is Working" Stuck State

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate all scenarios where the UI permanently shows "Agent is working" (spinner) after the agent has actually stopped, been cancelled, or the server has restarted.

**Architecture:** Fix state transitions across three layers: message buffer (backend), WebSocket recover loop (backend), and session state merging (frontend). Each fix is independently testable and committable.

**Tech Stack:** Python (FastAPI, SQLite), TypeScript (React), Vitest, pytest

---

## Already Completed (Previous Session)

These fixes are already merged and **do not need to be re-implemented**:

| Fix | Description | Files |
|-----|-------------|-------|
| Cancel awaits task completion | `cancel_session` now `await task` after `task.cancel()` | `main_server.py:1987-1999` |
| `mark_done()` preserves terminal state | Won't overwrite `cancelled`/`error` with `completed` | `src/message_buffer.py:317-324` |
| Frontend staleness check | `handleSelectSession` has 30s stale buffer guard | `frontend/src/App.tsx:877-913` |
| Staleness helper functions | `isFreshRunningState`, `isStaleRunningState` | `frontend/src/lib/session-state.ts` |

## Remaining Bugs (This Plan)

| # | Bug | Priority | Risk |
|---|-----|----------|------|
| 1 | `mark_done()` doesn't `event.set()` — consumers sleep up to 30s | P0 | Low |
| 2 | `_ensure_buf()` only restores from `result` message, not `session_state_changed` | P0 | Low |
| 3 | `STATE_ORDER` missing `cancelled` — merge returns wrong state | P0 | None |
| 4 | Recover loop exits without emitting terminal state | P1 | Low |
| 5 | Index guard blocks terminal state changes | P1 | Low |
| 6 | WebSocket disconnect doesn't reset running sessions | P1 | Low |
| 7 | Heartbeat masks agent death (no `agent_alive` flag) | P2 | Medium |

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/message_buffer.py:317-324` | Modify | Add `event.set()` to `mark_done()` |
| `src/message_buffer.py:54-91` | Modify | `_ensure_buf()` restore from `session_state_changed` |
| `frontend/src/lib/session-state.ts:15-21` | Modify | Add `cancelled` to `STATE_ORDER` |
| `main_server.py:1508-1523` | Modify | Recover loop emit terminal state |
| `main_server.py:1672-1689` | Modify | Main subscribe loop emit terminal state |
| `frontend/src/App.tsx:463,484,504,584,593` | Modify | Relax index guard for terminal states |
| `frontend/src/App.tsx:625-629` | Modify | Add `onDisconnect` callback |
| `src/message_buffer.py:26-31` | Modify | Add `agent_alive` to heartbeat |
| `main_server.py:1529,1695` | Modify | Set `agent_alive` based on task existence |
| `frontend/src/App.tsx` | Modify | Handle `agent_alive: false` heartbeat |
| `tests/unit/test_message_buffer.py` | Modify | Add tests for bugs 1, 2, 3 |
| `frontend/src/lib/session-state.test.ts` | Modify | Add test for `cancelled` in `STATE_ORDER` |

---

## Task 1: `mark_done()` wakes consumers

**Files:**
- Modify: `src/message_buffer.py:317-324`
- Test: `tests/unit/test_message_buffer.py`

**Why:** `mark_done()` sets `done = True` and `state = "completed"` but never calls `event.set()`. Consumers (subscribe loops) sleep for `HEARTBEAT_INTERVAL` (30s) before checking again. In edge cases where `add_message()` (which does call `event.set()`) fires *before* `mark_done()` and its event is consumed, the consumer goes back to sleep for 30s.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_message_buffer.py` in the `TestMarkDone` class:

```python
def test_mark_done_wakes_consumers(self, buffer: MessageBuffer) -> None:
    """mark_done() must wake up waiting consumers immediately, not wait
    for the next heartbeat."""
    import asyncio

    buffer.add_message("s1", {"type": "system", "subtype": "progress"})
    event = buffer.subscribe("s1")
    assert not event.is_set()

    buffer.mark_done("s1")

    # Consumer must be woken immediately — not wait 30s for heartbeat
    assert event.is_set(), "mark_done() did not wake consumers"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.local/bin/uv run pytest tests/unit/test_message_buffer.py::TestMarkDone::test_mark_done_wakes_consumers -v`
Expected: FAIL with "mark_done() did not wake consumers"

- [ ] **Step 3: Implement — add event.set() to mark_done()**

Modify `src/message_buffer.py:317-324`:

```python
def mark_done(self, session_id: str) -> None:
    self._ensure_buf(session_id)["done"] = True
    # Don't overwrite an already-set terminal state (e.g., 'cancelled'
    # from cancel()). Only set 'completed' if the session wasn't already
    # in a different terminal state.
    current_state = self.sessions[session_id].get("state", "idle")
    if current_state not in ("cancelled", "error"):
        self.sessions[session_id]["state"] = "completed"
    # Wake up waiting consumers so subscribe loop detects completion
    # immediately instead of waiting for the next 30s heartbeat.
    buf = self.sessions[session_id]
    for event in list(buf.get("consumers", set())):
        event.set()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/.local/bin/uv run pytest tests/unit/test_message_buffer.py::TestMarkDone::test_mark_done_wakes_consumers -v`
Expected: PASS

- [ ] **Step 5: Run full test_message_buffer.py to verify no regressions**

Run: `~/.local/bin/uv run pytest tests/unit/test_message_buffer.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/message_buffer.py tests/unit/test_message_buffer.py
git commit -m "fix: mark_done() wakes consumers to eliminate 30s detection delay"
```

---

## Task 2: `_ensure_buf()` restores terminal state from session_state_changed

**Files:**
- Modify: `src/message_buffer.py:54-91`
- Test: `tests/unit/test_message_buffer.py`

**Why:** After server restart, `_ensure_buf()` only restores state if the last DB message is `type == "result"`. If the agent crashed after writing `session_state_changed: error` but before writing `result`, the buffer returns `state: "idle"`. Frontend then sees DB history saying `running` + new buffer saying `idle` → `mergeSessionStates("idle", "running")` = `"running"` → stuck.

- [ ] **Step 1: Write the failing test for cancelled state**

Add to `tests/unit/test_message_buffer.py` in the `TestRestartRecovery` class:

```python
def test_ensure_buf_restores_cancelled_from_db(self, tmp_path: Path) -> None:
    """After restart, buffer should restore 'cancelled' state from DB
    even when there is no 'result' message."""
    db_path = tmp_path / "test.db"
    session_id = "cancelled-session"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  session_id TEXT NOT NULL,"
            "  seq INTEGER NOT NULL,"
            "  type TEXT NOT NULL,"
            "  subtype TEXT,"
            "  content TEXT,"
            "  payload TEXT,"
            "  created_at REAL NOT NULL DEFAULT 0"
            ")"
        )
        conn.execute(
            "INSERT INTO messages (session_id, seq, type, subtype, payload, created_at) "
            "VALUES (?, 0, 'system', 'session_state_changed', "
            "'{\"state\": \"cancelled\"}', ?)",
            (session_id, time.time()),
        )
        conn.commit()
    finally:
        conn.close()

    buf = MessageBuffer(
        base_dir=tmp_path / "buf",
        db=type("FakeDB", (), {"db_path": db_path})(),  # type: ignore[arg-type]
    )
    buf._sync_conn = sqlite3.connect(str(db_path))

    state = buf.get_session_state(session_id)
    assert state["state"] == "cancelled"
    assert buf.is_done(session_id) is True
```

- [ ] **Step 2: Write the failing test for error state**

```python
def test_ensure_buf_restores_error_from_db(self, tmp_path: Path) -> None:
    """After restart, buffer should restore 'error' state from DB."""
    db_path = tmp_path / "test.db"
    session_id = "error-session"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  session_id TEXT NOT NULL,"
            "  seq INTEGER NOT NULL,"
            "  type TEXT NOT NULL,"
            "  subtype TEXT,"
            "  content TEXT,"
            "  payload TEXT,"
            "  created_at REAL NOT NULL DEFAULT 0"
            ")"
        )
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
    finally:
        conn.close()

    buf = MessageBuffer(
        base_dir=tmp_path / "buf",
        db=type("FakeDB", (), {"db_path": db_path})(),  # type: ignore[arg-type]
    )
    buf._sync_conn = sqlite3.connect(str(db_path))

    state = buf.get_session_state(session_id)
    assert state["state"] == "error"
    assert buf.is_done(session_id) is True
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `~/.local/bin/uv run pytest tests/unit/test_message_buffer.py::TestRestartRecovery::test_ensure_buf_restores_cancelled_from_db tests/unit/test_message_buffer.py::TestRestartRecovery::test_ensure_buf_restores_error_from_db -v`
Expected: Both FAIL

- [ ] **Step 4: Implement — add session_state_changed recovery to _ensure_buf()**

Modify `src/message_buffer.py:54-91`. Replace the `_ensure_buf` method's DB restoration block (lines 67-88):

```python
def _ensure_buf(self, session_id: str) -> dict[str, Any]:
    """Lazy-initialise a session buffer, restoring terminal state from DB."""
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
                    # Check 2: if last message is a system message, look up
                    # the last session_state_changed for terminal state.
                    # Covers crash scenarios where result wasn't written.
                    elif row and row[0] == "system":
                        cursor2 = self._sync_conn.execute(
                            "SELECT payload FROM messages WHERE session_id = ? "
                            "AND type = 'system' AND subtype = 'session_state_changed' "
                            "ORDER BY seq DESC LIMIT 1",
                            (session_id,),
                        )
                        row2 = cursor2.fetchone()
                        if row2 and row2[0]:
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

- [ ] **Step 5: Run tests to verify they pass**

Run: `~/.local/bin/uv run pytest tests/unit/test_message_buffer.py::TestRestartRecovery -v`
Expected: All TestRestartRecovery tests PASS (including the two new ones)

- [ ] **Step 6: Commit**

```bash
git add src/message_buffer.py tests/unit/test_message_buffer.py
git commit -m "fix: _ensure_buf restores terminal state from session_state_changed on restart"
```

---

## Task 3: Add `cancelled` to STATE_ORDER

**Files:**
- Modify: `frontend/src/lib/session-state.ts:15-21`
- Test: `frontend/src/lib/session-state.test.ts`

**Why:** `cancelled` is missing from `STATE_ORDER`, so `mergeSessionStates()` returns `-1` for it. This means `mergeSessionStates("idle", "cancelled")` returns `"idle"` (0 > -1), losing the cancelled state after refresh.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/lib/session-state.test.ts`:

```typescript
describe('cancelled state handling', () => {
  it('mergeSessionStates handles cancelled as terminal state', () => {
    // cancelled should beat idle (it's a terminal state)
    expect(mergeSessionStates('idle', 'cancelled')).toBe('cancelled')
    // cancelled should be preferred over completed (both terminal, cancelled is more recent)
    expect(mergeSessionStates('cancelled', 'completed')).toBe('cancelled')
  })

  it('STATE_ORDER includes cancelled', () => {
    expect(STATE_ORDER['cancelled']).toBe(3)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/session-state.test.ts`
Expected: FAIL — `STATE_ORDER['cancelled']` is `undefined`

- [ ] **Step 3: Implement — add cancelled to STATE_ORDER**

Modify `frontend/src/lib/session-state.ts:15-21`:

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

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/session-state.test.ts`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/session-state.ts frontend/src/lib/session-state.test.ts
git commit -m "fix: add cancelled to STATE_ORDER so merge handles it as terminal state"
```

---

## Task 4: Recover loop emits terminal state when done

**Files:**
- Modify: `main_server.py:1508-1523` (recover loop)
- Modify: `main_server.py:1672-1689` (main subscribe loop)

**Why:** When `is_done()` is True but `session_state_changed` was already sent in a previous pull, the recover loop sends remaining messages and exits without sending a state change. Frontend never transitions away from "running".

- [ ] **Step 1: Implement — add terminal state emission to recover loop**

Modify `main_server.py:1508-1523`. Replace the `if buffer.is_done(session_id):` block:

```python
# If session is done, final pull and exit
if buffer.is_done(session_id):
    final_messages = buffer.get_history(session_id, after_index=last_seen)
    for i, h in enumerate(final_messages):
        idx = last_seen + i
        await websocket.send_text(
            json.dumps(
                {
                    **h,
                    "index": idx,
                    "replay": False,
                    "session_id": session_id,
                }
            )
        )
    last_seen += len(final_messages)

    # Safety: if buffer state is terminal but no state_change was in
    # the final pull, emit one so the frontend can transition away
    # from "running".
    buf_state = buffer.get_session_state(session_id)
    if buf_state["state"] in ("completed", "error", "cancelled"):
        has_state_change = any(
            m.get("type") == "system"
            and m.get("subtype") == "session_state_changed"
            for m in final_messages
        )
        if not has_state_change:
            await websocket.send_text(
                json.dumps({
                    "type": "system",
                    "subtype": "session_state_changed",
                    "state": buf_state["state"],
                    "index": last_seen,
                    "replay": False,
                    "session_id": session_id,
                })
            )
    break
```

- [ ] **Step 2: Implement — same fix for main subscribe loop**

Modify `main_server.py:1672-1689`. Replace the `if buffer.is_done(session_id):` block in the main subscribe loop (around line 1675) with the same logic:

```python
# If session is done, pull one final time to ensure
# session_state_changed: completed is not missed
# (it may have been added after the get_history snapshot).
if buffer.is_done(session_id):
    final_messages = buffer.get_history(session_id, after_index=last_seen)
    for i, h in enumerate(final_messages):
        idx = last_seen + i
        await websocket.send_text(
            json.dumps(
                {
                    **h,
                    "index": idx,
                    "replay": False,
                    "session_id": session_id,
                }
            )
        )
    last_seen += len(final_messages)

    # Safety: emit terminal state if not already in final_messages
    buf_state = buffer.get_session_state(session_id)
    if buf_state["state"] in ("completed", "error", "cancelled"):
        has_state_change = any(
            m.get("type") == "system"
            and m.get("subtype") == "session_state_changed"
            for m in final_messages
        )
        if not has_state_change:
            await websocket.send_text(
                json.dumps({
                    "type": "system",
                    "subtype": "session_state_changed",
                    "state": buf_state["state"],
                    "index": last_seen,
                    "replay": False,
                    "session_id": session_id,
                })
            )
    break
```

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `~/.local/bin/uv run pytest tests/unit/test_main_server.py::TestCancelSession tests/unit/test_main_server.py::TestSessionStatus -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add main_server.py
git commit -m "fix: recover and subscribe loops emit terminal state when done"
```

---

## Task 5: Relax index guard for terminal state changes

**Files:**
- Modify: `frontend/src/App.tsx` (5 locations around lines 463, 484, 504, 584, 593)

**Why:** `session_state_changed` messages with `index < highestUserMsgIndexRef.current` are dropped. For terminal states (`completed`/`error`/`cancelled`), this can prevent the UI from transitioning away from "running" if the state message arrives with a slightly lower index than expected.

- [ ] **Step 1: Define TERMINAL_STATES constant near top of handleIncomingMessage**

In `frontend/src/App.tsx`, find the `handleIncomingMessage` callback. Add this constant at the start of the function (or at module level if preferred):

```typescript
const TERMINAL_STATES = new Set(["completed", "error", "cancelled"]);
```

- [ ] **Step 2: Relax invisible message path index guard (line ~463)**

Find this block:
```typescript
if (msg.index != null && msg.index < highestUserMsgIndexRef.current) {
  // Skip — this state change is older than the current run's user message
}
```

Replace with:
```typescript
const newState = msg.state || msg.content || "completed";
const isTerminal = TERMINAL_STATES.has(newState);
if (msg.index != null && msg.index < highestUserMsgIndexRef.current && !isTerminal) {
  // Skip — this state change is older than the current run's user message
  // (except terminal states which should always pass through)
}
```

- [ ] **Step 3: Relax result message path index guard (line ~484)**

Find this block:
```typescript
if (msg.type === "result" && !msg.replay && msg.session_id) {
  if (
    msg.index == null ||
    msg.index >= highestUserMsgIndexRef.current
  ) {
    setSessionStateFor(msg.session_id, "completed");
```

Replace with:
```typescript
if (msg.type === "result" && !msg.replay && msg.session_id) {
  if (
    msg.index == null ||
    msg.index >= highestUserMsgIndexRef.current ||
    true  // result is always terminal — always accept
  ) {
    setSessionStateFor(msg.session_id, "completed");
```

Actually, simplify — just remove the guard for result messages in the invisible path:
```typescript
if (msg.type === "result" && !msg.replay && msg.session_id) {
  setSessionStateFor(msg.session_id, "completed");
```

- [ ] **Step 4: Relax visible message path index guard (line ~584)**

Find:
```typescript
if (msg.index == null || msg.index >= highestUserMsgIndexRef.current) {
  setSessionStateFor(
    msg.session_id,
    msg.state || msg.content || "completed",
  );
}
```

Replace with:
```typescript
const newState = msg.state || msg.content || "completed";
const isTerminal = TERMINAL_STATES.has(newState);
if (msg.index == null || msg.index >= highestUserMsgIndexRef.current || isTerminal) {
  setSessionStateFor(msg.session_id, newState);
}
```

- [ ] **Step 5: Relax result message visible path guard (line ~593)**

Find:
```typescript
if (msg.index == null || msg.index >= highestUserMsgIndexRef.current) {
  setSessionStateFor(msg.session_id, "completed");
}
```

Replace with (result is always terminal):
```typescript
setSessionStateFor(msg.session_id, "completed");
```

- [ ] **Step 6: Run frontend tests**

Run: `cd frontend && npx vitest run`
Expected: All 321+ tests PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "fix: relax index guard to allow terminal state changes through"
```

---

## Task 6: WebSocket disconnect resets running sessions

**Files:**
- Modify: `frontend/src/App.tsx:625-629`

**Why:** When WebSocket disconnects, `useWebSocket` calls `onDisconnect?.()` but App.tsx never passes this callback. Running sessions stay "running" forever after disconnect.

- [ ] **Step 1: Implement — add onDisconnect callback**

Find the `useWebSocket` call in `frontend/src/App.tsx` (around line 625):

```typescript
} = useWebSocket({
    userId,
    onMessage: handleIncomingMessage,
    token: authToken ?? undefined,
});
```

Replace with:
```typescript
} = useWebSocket({
    userId,
    onMessage: handleIncomingMessage,
    onDisconnect: () => {
      // Reset all running/waiting sessions to idle — the agent tasks
      // are no longer connected to this client. The next recover
      // will restore the correct state.
      for (const [sid, state] of sessionStatesRef.current) {
        if (state === "running" || state === "waiting_user") {
          setSessionStateFor(sid, "idle");
        }
      }
    },
    token: authToken ?? undefined,
});
```

- [ ] **Step 2: Run frontend tests**

Run: `cd frontend && npx vitest run`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "fix: reset running sessions to idle on WebSocket disconnect"
```

---

## Task 7: Heartbeat includes agent_alive flag

**Files:**
- Modify: `src/message_buffer.py:26-31`
- Modify: `main_server.py:1529,1695`
- Modify: `frontend/src/App.tsx` (heartbeat handler)

**Why:** Even after agent dies, the subscribe loop keeps sending heartbeats every 30s. Frontend's stale detection (60s gap) is reset by each heartbeat, so it never triggers recovery.

- [ ] **Step 1: Add agent_alive parameter to make_heartbeat()**

Modify `src/message_buffer.py:26-31`:

```python
def make_heartbeat(agent_alive: bool = True) -> dict[str, Any]:
    """Create a heartbeat message to signal the session is still alive."""
    return {
        "type": "heartbeat",
        "timestamp": time.time(),
        "agent_alive": agent_alive,
    }
```

- [ ] **Step 2: Set agent_alive=False in recover loop heartbeat**

In the recover loop's heartbeat timeout handler (around line 1529), check if the session still has an active task:

Find:
```python
except asyncio.TimeoutError:
    hb = make_heartbeat()
    await websocket.send_text(
```

Replace with:
```python
except asyncio.TimeoutError:
    agent_alive = f"task_{session_id}" in active_tasks and not active_tasks[f"task_{session_id}"].done()
    hb = make_heartbeat(agent_alive=agent_alive)
    await websocket.send_text(
```

- [ ] **Step 3: Same fix for main subscribe loop (around line 1695)**

Find:
```python
except asyncio.TimeoutError:
    hb = make_heartbeat()
    await websocket.send_text(
```

Replace with:
```python
except asyncio.TimeoutError:
    agent_alive = f"task_{session_id}" in active_tasks and not active_tasks[f"task_{session_id}"].done()
    hb = make_heartbeat(agent_alive=agent_alive)
    await websocket.send_text(
```

- [ ] **Step 4: Frontend handles agent_alive: false**

In `frontend/src/App.tsx`, find the heartbeat handler in `handleIncomingMessage`. Add this check:

```typescript
if (msg.type === "heartbeat" && msg.agent_alive === false) {
  // Agent task no longer exists on backend — trigger recovery
  // to discover the real state
  if (activeSessionRef.current) {
    sendRecover(
      activeSessionRef.current,
      computeRecoverIndex(messages as unknown as Message[]),
    );
  }
}
```

- [ ] **Step 5: Run all tests**

Backend: `~/.local/bin/uv run pytest tests/unit/test_message_buffer.py tests/unit/test_main_server.py::TestCancelSession tests/unit/test_main_server.py::TestSessionStatus -v`
Frontend: `cd frontend && npx vitest run`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/message_buffer.py main_server.py frontend/src/App.tsx
git commit -m "fix: heartbeat includes agent_alive flag to detect dead agents"
```

---

## Implementation Order

| # | Task | Priority | Estimated effort |
|---|------|----------|-----------------|
| 1 | `mark_done()` wakes consumers | P0 | 10 min |
| 2 | `_ensure_buf()` restores terminal state | P0 | 20 min |
| 3 | `STATE_ORDER` includes `cancelled` | P0 | 5 min |
| 4 | Recover loop emits terminal state | P1 | 15 min |
| 5 | Relax index guard for terminal states | P1 | 15 min |
| 6 | WebSocket disconnect resets sessions | P1 | 5 min |
| 7 | Heartbeat agent_alive flag | P2 | 15 min |

Each task is independently testable and committable. Tasks 1-3 are P0 (must-fix), 4-6 are P1 (should-fix), 7 is P2 (nice-to-have).

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `_ensure_buf` restores stale terminal state over active session | Low | High | Only runs on first access (`session_id not in self.sessions`); `done=True` prevents re-trigger |
| `mark_done()` event.set() causes premature exit | Low | Medium | Wakes only after `done=True` and `state` are set — consumer sees terminal state |
| Terminal state bypassing index guard overwrites newer state | Low | High | Only `completed`/`error`/`cancelled` bypass — all are terminal, never downgrade |
| `onDisconnect` resets state that recover would fix anyway | Medium | Low | Reset is correct behavior; recover restores proper state on reconnect |

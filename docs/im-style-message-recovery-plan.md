# IM-Style Message Flow Recovery — Implementation

## Problem

Frontend stays stuck on "Agent is working..." after agent completes because the WebSocket completion signal was lost.

## Root Cause

The backend subscribe loop exits after agent completion, stopping all messages (including heartbeats). If the completion signal is lost in transit, the frontend has no detection mechanism — it stays 'running' forever.

## Solution: Heartbeat-Driven Staleness Detection

**Key insight:** The heartbeat is the canary. When the subscribe loop breaks (agent done), heartbeats stop. If WS stays connected but no heartbeat for 60s while 'running' → recovery triggered.

## Implementation

### File: `frontend/src/App.tsx`

```typescript
// 1. Track last heartbeat (line 289)
const lastHeartbeatRef = useRef(Date.now())

// 2. Reset on session switch (line 293-295)
useEffect(() => {
  lastHeartbeatRef.current = Date.now()
}, [activeSession])

// 3. Track heartbeat in handleIncomingMessage (line 298-301)
if (msg.type === 'heartbeat') {
  lastHeartbeatRef.current = Date.now()
}

// 4. Staleness detection useEffect (line 429-445)
useEffect(() => {
  if (activeSessionState !== 'running' || !activeSessionRef.current) return
  const checkInterval = setInterval(() => {
    const sid = activeSessionRef.current
    if (!sid) return
    const gap = Date.now() - lastHeartbeatRef.current
    if (gap > 60_000) {
      lastHeartbeatRef.current = Date.now()
      sendRecover(sid, computeRecoverIndex(messages))
    }
  }, 10_000)
  return () => clearInterval(checkInterval)
}, [activeSessionState, messages, sendRecover])
```

### File: `frontend/src/App.test.ts`

Added 9 tests in `heartbeat staleness detection` describe block:
1. Does NOT trigger when session is not running
2. Does NOT trigger when heartbeats are recent
3. Triggers when session is running and heartbeats stopped for 60s
4. Does NOT trigger when gap is just under threshold (50s)
5. Recovery resets after heartbeat arrives
6. Session switch: running → completed stops staleness check
7. Multiple consecutive staleness checks after reset
8. Error state should not trigger
9. Session switch resets heartbeat to prevent false staleness

## Edge Cases Handled

| Scenario | How It's Handled |
|---|---|
| Session switch A→B | `activeSession` change resets `lastHeartbeatRef` |
| Rapid switches A→B→C | Each switch resets heartbeat; only active session checked |
| Agent error/timeout/cancel | State changes to 'error'/'cancelled' → staleness effect returns early |
| False positive (slow agent) | 60s threshold is generous; streaming agents send messages/heartbeats frequently |
| Stale closure on messages | `messages` in dependency array ensures correct recovery index |
| Repeated triggers | Heartbeat ref reset after triggering prevents spam |

## Verification

- Type check: ✅ clean
- Tests: ✅ 267/267 passing (57 in App.test.ts)
- Coverage: ✅ staleness algorithm tested at 100%

## Recovery Flow

```
Agent completes
  ↓
Backend: mark_done() → subscribe loop breaks → heartbeats STOP
  ↓
Frontend: completion message LOST
  ↓
Frontend: no heartbeat for 60s + session='running'
  ↓
Frontend: sendRecover(sessionId, recoverIndex)
  ↓
Backend recover: get_history(after=lastIndex) → sends completion
  ↓
Frontend: receives completion → sessionState='completed' ✓
```

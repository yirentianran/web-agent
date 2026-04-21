# Streaming Output Fix Plan

## Problem Restatement

After page refresh, the UI can get stuck showing "Agent is working..." indefinitely because:
1. Live buffer reports `running` state but the agent has already exited
2. No `session_state_changed` or `result` message arrives to transition the UI to `completed`/`idle`
3. A 60s heartbeat staleness fallback exists but is too slow for a good UX

## Additional Issues Found

| # | Issue | Severity | Status | Files |
|---|-------|----------|--------|-------|
| 1 | Buffer expired → stuck running | HIGH | DONE | `App.tsx`, `main_server.py`, `src/models.py`, `src/message_buffer.py` |
| 2 | `console.log` in production code | MEDIUM | DONE | `useStreamingText.ts`, `useWebSocket.ts`, `App.tsx`, `ChatArea.tsx` |
| 3 | `msg: any` type unsafe | MEDIUM | DONE | `useStreamingText.ts` |
| 4 | Streaming text may flicker one frame | LOW | SKIPPED | React 18 batching makes it negligible |
| 5 | Multiple `useEffect` overlap in ChatArea | LOW | SKIPPED | Low signal, risk of introducing timing bugs |
| 6 | Timer resets to 0 after page refresh | MEDIUM | DONE | `StatusSpinner.tsx` |

## Completed Changes

### Phase 1: Buffer Expiry → Stuck Running (HIGH)

**Done**:
- `src/message_buffer.py` — Added `buffer_age` (seconds since last activity) to `get_session_state()` return dict
- `src/models.py` — Added `buffer_age: float` field to `SessionStatusResponse`
- `main_server.py` — Status endpoint now returns `buffer_age`
- `App.tsx` — On page load, if buffer says `running` but `buffer_age >= 30s`, triggers recovery instead of setting state to `running`

### Phase 2: Remove console.log from Production (MEDIUM)

**Done**: Removed all `console.log` debug statements from:
- `useStreamingText.ts` (2 removed)
- `useWebSocket.ts` (1 removed)
- `App.tsx` (2 removed)
- `ChatArea.tsx` (7 removed including loadStartTimes/saveStartTimes/debug useEffect)

### Phase 3: Type Safety in useStreamingText (MEDIUM)

**Done**:
- Changed `msg: any` → `msg: unknown`
- Added type guards: `isStreamEvent`, `isTextDelta`, `isMessageStop`, `isAssistantMessage`, `isResultMessage`
- Added proper interface types for delta events
- Fixed `reset()` function signature (removed unused parameter)
- Fixed test type (`getState` → `createInitialState`)

### Phase 6: Timer Resets to 0 After Page Refresh (MEDIUM)

**Done**:
- `StatusSpinner.tsx` — `useState` initialized with `Date.now() - startTime` instead of `0`
- `StatusSpinner.tsx` — Removed `setElapsed(0)` from useEffect, replaced with correct initial value
- Added `data-testid="elapsed"` for testing
- Added 6 component tests covering immediate display, time progression, startTime changes, stale styling

## Test Plan

- [x] Frontend: 312 tests pass (17 test files)
- [x] Frontend: no new TypeScript errors
- [x] Frontend: StatusSpinner component tests pass (10 total)
- [x] Frontend: useStreamingText tests pass (10 total)
- [ ] Backend: buffer_age endpoint test (pending - uv tests timeout)
- [ ] Manual: verify timer survives page refresh without "0秒" flash
- [ ] Manual: verify stale buffer triggers recover, not running state

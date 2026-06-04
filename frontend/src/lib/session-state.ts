/**
 * Session state merging and recovery utilities.
 *
 * Fixes race conditions during session switching:
 * 1. Merges DB-derived state with live buffer state, preferring the
 *    more "active" state to avoid stale 'idle' overwriting 'running'.
 * 2. Computes correct recover index from loaded messages.
 */

/**
 * Ordering of session states by "activity" level.
 * Higher number = more active or terminal.
 * Used to decide which state wins when merging.
 */
export const STATE_ORDER: Record<string, number> = {
  idle: 0,
  completed: 1,
  running: 2,
  waiting_user: 2,
  error: 3,
  cancelled: 3,
} as const

/**
 * Merge a live buffer state with a DB-derived state.
 *
 * When switching sessions, the DB history may not contain the latest
 * state-change message (e.g., `running` or `completed`) because it
 * hasn't been flushed to SQLite yet. The status endpoint checks the
 * in-memory buffer and may return a more recent state.
 *
 * We prefer the more "active" state to avoid scenarios where:
 * - DB says 'idle' but buffer says 'running' → prefer 'running'
 * - History says 'completed' but stale poll says 'running' → prefer 'running'
 *   (the recover will deliver the actual completed message shortly)
 *
 * @param bufferState - State from the live buffer status endpoint
 * @param dbState - State derived from DB history messages
 * @returns The merged state (more active wins)
 */
/**
 * @deprecated Use `resolveSessionState` instead — this function has
 * different semantics (prefers more "active" state, including stale
 * "running" over terminal). `resolveSessionState` correctly handles
 * terminal overrides and stale buffer detection.
 */
export function mergeSessionStates(
  bufferState: string | undefined,
  dbState: string,
): string {
  if (!bufferState) return dbState

  const bufferOrder = STATE_ORDER[bufferState] ?? -1
  const dbOrder = STATE_ORDER[dbState] ?? 0

  return bufferOrder > dbOrder ? bufferState : dbState
}

/**
 * Staleness threshold (seconds) — if the buffer hasn't been updated in
 * this long, a "running" state is likely stale (agent already exited).
 */
export const STALE_BUFFER_THRESHOLD = 30

/**
 * Terminal session states — once reached, the agent is no longer running.
 */
export const TERMINAL_STATES = new Set(["completed", "error", "cancelled"])

/**
 * States that represent a real transition away from "running".
 * Includes terminal states plus "waiting_user" (agent paused for input).
 */
export const NON_RUNNING_STATES = new Set(["completed", "error", "cancelled", "waiting_user"])

/**
 * Check whether a "running" buffer state should be trusted.
 *
 * On page mount the staleness check already exists. This function
 * centralises the logic so it can also be used during session switching.
 *
 * @param state - Buffer state from status endpoint
 * @param bufferAge - Seconds since the buffer was last active
 * @returns true if the running state is fresh and should be trusted
 */
export function isFreshRunningState(
  state: string | undefined,
  bufferAge: number,
): boolean {
  return state === 'running' && bufferAge < STALE_BUFFER_THRESHOLD
}

/**
 * Check whether a buffer's running state is stale (likely agent already exited).
 *
 * @param state - Buffer state from status endpoint
 * @param bufferAge - Seconds since the buffer was last active
 * @returns true if the running state is stale and should NOT be trusted
 */
export function isStaleRunningState(
  state: string | undefined,
  bufferAge: number,
): boolean {
  return state === 'running' && bufferAge >= STALE_BUFFER_THRESHOLD
}

/**
 * Resolve session state from multiple authoritative sources.
 *
 * Used when loading a session via REST history + /status endpoint.
 * Handles race conditions between stale frontend state, DB-derived state,
 * and live buffer state.
 *
 * @param currentState - Current frontend state from sessionStatesRef
 * @param derivedState - State derived from REST history messages
 * @param bufferState - State from /status endpoint (live buffer)
 * @param bufferAge - Seconds since buffer was last active
 * @returns The resolved state and whether recovery should be triggered
 */
export function resolveSessionState(
  currentState: string,
  derivedState: string,
  bufferState?: string,
  bufferAge?: number,
): { state: string; shouldRecover: boolean } {

  // Step 1: Resolve history-derived state against current frontend state
  let resolvedState: string;

  if (currentState === "running" && derivedState !== "running") {
    if (NON_RUNNING_STATES.has(derivedState)) {
      // Accept non-running derived state over stale frontend "running" —
      // the session has completed, errored, been cancelled, or is waiting
      // for user input while the user was on a different session.
      resolvedState = derivedState;
    } else {
      // Preserve live "running" — don't downgrade to "idle" from stale history
      resolvedState = currentState;
    }
  } else {
    resolvedState = derivedState;
  }

  // Step 2: Apply /status endpoint correction
  if (bufferState) {
    const age = bufferAge ?? 0;

    if (bufferState === "running" && age < STALE_BUFFER_THRESHOLD) {
      // Fresh "running" from buffer — accept it
      resolvedState = "running";
    } else if (bufferState === "running" && age >= STALE_BUFFER_THRESHOLD) {
      // Stale "running" from buffer — trigger recovery, don't accept stale state
      return { state: resolvedState, shouldRecover: true };
    } else if (TERMINAL_STATES.has(bufferState) && resolvedState === "running") {
      // Buffer confirms terminal — override stale frontend "running"
      resolvedState = bufferState;
    }
    // For other buffer states (idle, waiting_user), accept if the buffer
    // reports a more active state than what we resolved from history.
    // This handles the case where DB hasn't flushed "waiting_user" yet
    // but the live buffer has it.
    else if (bufferState !== "idle" && bufferState !== resolvedState) {
      const bufferOrder = STATE_ORDER[bufferState] ?? -1;
      const resolvedOrder = STATE_ORDER[resolvedState] ?? 0;
      if (bufferOrder > resolvedOrder) {
        resolvedState = bufferState;
      }
    }
  }

  return { state: resolvedState, shouldRecover: false };
}

/**
 * Apply /status endpoint correction to an already-resolved session state.
 *
 * Used after `resolveSessionState` has handled the history-derived merge.
 * This skips Step 1 (history vs current) since that was already processed,
 * and only applies the /status buffer correction (Step 2).
 *
 * @param currentState - Already-resolved state (from history merge)
 * @param bufferState - State from /status endpoint (live buffer)
 * @param bufferAge - Seconds since buffer was last active
 * @returns The resolved state and whether recovery should be triggered
 */
export function resolveBufferState(
  currentState: string,
  bufferState: string,
  bufferAge: number,
): { state: string; shouldRecover: boolean } {
  if (bufferState === "running" && bufferAge < STALE_BUFFER_THRESHOLD) {
    return { state: "running", shouldRecover: false };
  }
  if (bufferState === "running" && bufferAge >= STALE_BUFFER_THRESHOLD) {
    return { state: currentState, shouldRecover: true };
  }
  if (TERMINAL_STATES.has(bufferState) && currentState === "running") {
    return { state: bufferState, shouldRecover: false };
  }
  if (bufferState !== "idle" && bufferState !== currentState) {
    const bufferOrder = STATE_ORDER[bufferState] ?? -1;
    const currentOrder = STATE_ORDER[currentState] ?? 0;
    if (bufferOrder > currentOrder) {
      return { state: bufferState, shouldRecover: false };
    }
  }
  return { state: currentState, shouldRecover: false };
}

/**
 * Compute the correct recover index from loaded messages.
 *
 * The recover index should be one past the highest message index the
 * frontend currently has, so the backend sends only newer messages.
 *
 * Previously, `sendRecover(id, msgs.length)` was used, which is wrong
 * when messages have non-contiguous indices (e.g., optimistic messages
 * at index -1, or after replay).
 *
 * @param messages - Array of loaded messages with `index` field
 * @returns The index to recover from (max index + 1, or 0 if empty)
 */
export function computeRecoverIndex(messages: Array<{ index: number }>): number {
  if (messages.length === 0) return 0

  let maxIndex = messages[0].index
  for (let i = 1; i < messages.length; i++) {
    if (messages[i].index > maxIndex) {
      maxIndex = messages[i].index
    }
  }
  return maxIndex + 1
}

// ── last_known_index persistence (localStorage) ──────────────────

const LAST_INDEX_KEY_PREFIX = 'web-agent-last-index:'

/**
 * Build the localStorage key for a given session + user.
 */
function makeLastIndexKey(sessionId: string, userId: string): string {
  return `${LAST_INDEX_KEY_PREFIX}${userId}:${sessionId}`
}

/**
 * Persist the highest known message index for a session.
 * Written lazily (on session switch / unload) to avoid per-message writes.
 */
export function saveLastKnownIndex(sessionId: string, index: number, userId: string): void {
  try {
    localStorage.setItem(makeLastIndexKey(sessionId, userId), String(index))
  } catch {
    // localStorage full or unavailable — skip silently
  }
}

/**
 * Read the last known index for a session. Returns 0 if not found.
 */
export function loadLastKnownIndex(sessionId: string, userId: string): number {
  try {
    const raw = localStorage.getItem(makeLastIndexKey(sessionId, userId))
    if (raw === null) return 0
    const parsed = Number(raw)
    return Number.isFinite(parsed) ? parsed : 0
  } catch {
    return 0
  }
}

/**
 * Remove the persisted index for a session (e.g., on delete).
 */
export function clearLastKnownIndex(sessionId: string, userId: string): void {
  try {
    localStorage.removeItem(makeLastIndexKey(sessionId, userId))
  } catch {
    // ignore
  }
}

// ── pending message persistence (localStorage) ──────────────────

const PENDING_MSG_KEY_PREFIX = 'web-agent-pending-msg:'

interface StoredPendingMessage {
  content: string
  clientMsgId: string
  files?: Array<{ filename: string; size: number }>
  timestamp: number
}

function makePendingMsgKey(sessionId: string, userId: string): string {
  return `${PENDING_MSG_KEY_PREFIX}${userId}:${sessionId}`
}

export function savePendingMessage(
  sessionId: string,
  userId: string,
  msg: StoredPendingMessage,
): void {
  try {
    localStorage.setItem(
      makePendingMsgKey(sessionId, userId),
      JSON.stringify(msg),
    )
  } catch {
    // localStorage full or unavailable
  }
}

export function loadPendingMessage(
  sessionId: string,
  userId: string,
): StoredPendingMessage | null {
  try {
    const raw = localStorage.getItem(makePendingMsgKey(sessionId, userId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as StoredPendingMessage
    // Discard pending messages older than 5 minutes
    if (Date.now() - parsed.timestamp > 300_000) {
      localStorage.removeItem(makePendingMsgKey(sessionId, userId))
      return null
    }
    return parsed
  } catch {
    return null
  }
}

export function clearPendingMessage(
  sessionId: string,
  userId: string,
): void {
  try {
    localStorage.removeItem(makePendingMsgKey(sessionId, userId))
  } catch {
    // ignore
  }
}

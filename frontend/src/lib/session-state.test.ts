import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mergeSessionStates, computeRecoverIndex, STATE_ORDER, saveLastKnownIndex, loadLastKnownIndex, clearLastKnownIndex } from '../lib/session-state'

describe('mergeSessionStates', () => {
  it('prefers running over idle', () => {
    expect(mergeSessionStates('running', 'idle')).toBe('running')
  })

  it('prefers running over completed (race: poll returns stale running)', () => {
    // If the live buffer still says running but DB already has completed,
    // prefer running to avoid the spinner disappearing prematurely during
    // an active session. The recover will deliver the completed message shortly.
    expect(mergeSessionStates('running', 'completed')).toBe('running')
  })

  it('prefers error over everything', () => {
    expect(mergeSessionStates('error', 'running')).toBe('error')
    expect(mergeSessionStates('error', 'idle')).toBe('error')
    expect(mergeSessionStates('error', 'completed')).toBe('error')
  })

  it('prefers waiting_user over idle and completed', () => {
    expect(mergeSessionStates('waiting_user', 'idle')).toBe('waiting_user')
    expect(mergeSessionStates('waiting_user', 'completed')).toBe('waiting_user')
  })

  it('returns DB state when buffer state is idle', () => {
    expect(mergeSessionStates('idle', 'completed')).toBe('completed')
    expect(mergeSessionStates('idle', 'running')).toBe('running')
  })

  it('returns idle when both states are idle', () => {
    expect(mergeSessionStates('idle', 'idle')).toBe('idle')
  })

  it('returns completed when both agree', () => {
    expect(mergeSessionStates('completed', 'completed')).toBe('completed')
  })

  it('handles unknown buffer state gracefully', () => {
    // Unknown states should fall back to DB state
    expect(mergeSessionStates('unknown' as any, 'completed')).toBe('completed')
    expect(mergeSessionStates('unknown' as any, 'running')).toBe('running')
  })

  it('uses DB fallback when buffer state is missing', () => {
    expect(mergeSessionStates(undefined, 'completed')).toBe('completed')
    expect(mergeSessionStates(undefined, 'idle')).toBe('idle')
  })
})

describe('STATE_ORDER', () => {
  it('orders states by "activity" level', () => {
    // Higher number = more active/terminal
    expect(STATE_ORDER['idle']).toBe(0)
    expect(STATE_ORDER['completed']).toBe(1)
    expect(STATE_ORDER['running']).toBe(2)
    expect(STATE_ORDER['waiting_user']).toBe(2)
    expect(STATE_ORDER['error']).toBe(3)
  })
})

describe('computeRecoverIndex', () => {
  it('returns 0 for empty message array', () => {
    expect(computeRecoverIndex([])).toBe(0)
  })

  it('returns max index + 1 for messages with positive indices', () => {
    const messages = [
      { type: 'user', content: 'Hello', index: 0 },
      { type: 'assistant', content: 'Hi', index: 1 },
      { type: 'user', content: 'Next', index: 3 },
    ]
    expect(computeRecoverIndex(messages)).toBe(4)
  })

  it('handles negative indices (optimistic messages)', () => {
    const messages = [
      { type: 'user', content: 'Hello', index: -1 },
      { type: 'assistant', content: 'Hi', index: 0 },
    ]
    expect(computeRecoverIndex(messages)).toBe(1)
  })

  it('handles single message', () => {
    const messages = [{ type: 'user', content: 'Hello', index: 5 }]
    expect(computeRecoverIndex(messages)).toBe(6)
  })

  it('handles unsorted messages', () => {
    const messages = [
      { type: 'user', content: 'Third', index: 10 },
      { type: 'assistant', content: 'First', index: 2 },
      { type: 'user', content: 'Second', index: 5 },
    ]
    expect(computeRecoverIndex(messages)).toBe(11)
  })
})

// ── last_known_index persistence ──────────────────────────────────

describe('last_known_index persistence', () => {
  const TEST_USER_ID = 'test-user'

  beforeEach(() => {
    // Clean up any previous test data
    clearLastKnownIndex('test-session', TEST_USER_ID)
  })

  it('returns 0 when no index has been saved', () => {
    expect(loadLastKnownIndex('test-session', TEST_USER_ID)).toBe(0)
  })

  it('saves and loads index', () => {
    saveLastKnownIndex('test-session', 42, TEST_USER_ID)
    expect(loadLastKnownIndex('test-session', TEST_USER_ID)).toBe(42)
  })

  it('overwrites previous value', () => {
    saveLastKnownIndex('test-session', 10, TEST_USER_ID)
    saveLastKnownIndex('test-session', 99, TEST_USER_ID)
    expect(loadLastKnownIndex('test-session', TEST_USER_ID)).toBe(99)
  })

  it('isolates different sessions', () => {
    saveLastKnownIndex('session-a', 5, TEST_USER_ID)
    saveLastKnownIndex('session-b', 20, TEST_USER_ID)
    expect(loadLastKnownIndex('session-a', TEST_USER_ID)).toBe(5)
    expect(loadLastKnownIndex('session-b', TEST_USER_ID)).toBe(20)
  })

  it('isolates different users', () => {
    saveLastKnownIndex('session-a', 10, 'user-1')
    saveLastKnownIndex('session-a', 20, 'user-2')
    expect(loadLastKnownIndex('session-a', 'user-1')).toBe(10)
    expect(loadLastKnownIndex('session-a', 'user-2')).toBe(20)
  })

  it('clear removes the key', () => {
    saveLastKnownIndex('test-session', 42, TEST_USER_ID)
    clearLastKnownIndex('test-session', TEST_USER_ID)
    expect(loadLastKnownIndex('test-session', TEST_USER_ID)).toBe(0)
  })

  it('handles localStorage setItem unavailable gracefully', () => {
    // Clear any existing value first
    clearLastKnownIndex('test-session', TEST_USER_ID)

    // Use Object.defineProperty to properly mock setItem
    const originalSetItem = Object.getOwnPropertyDescriptor(Storage.prototype, 'setItem')
    Object.defineProperty(Storage.prototype, 'setItem', {
      value: vi.fn().mockImplementation(() => {
        throw new Error('QuotaExceededError')
      }),
      configurable: true,
      writable: true,
    })

    saveLastKnownIndex('test-session', 42, TEST_USER_ID)
    // Since setItem throws, the value is never written.
    // load uses real getItem which returns null → 0
    expect(loadLastKnownIndex('test-session', TEST_USER_ID)).toBe(0)

    // Restore
    if (originalSetItem) {
      Object.defineProperty(Storage.prototype, 'setItem', originalSetItem)
    }
  })
})

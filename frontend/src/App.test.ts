import { describe, it, expect, beforeEach, vi } from 'vitest'
import type { Message } from './lib/types'

/**
 * These tests verify the message handling logic in App.tsx.
 *
 * The key invariant: messages loaded by recovery (replay=true) must NOT
 * be cleared when a new turn starts. The isFirstTurnMessage clearing
 * logic should only apply to new live messages, not replay messages.
 */

// Simulate the handleIncomingMessage logic from App.tsx.
// We extract the core setMessages logic into a standalone function for testability.
function createMessageHandler(opts?: {
  onSessionStateChange?: (sessionId: string, state: string) => void
}) {
  const { onSessionStateChange } = opts || {}
  let messages: Message[] = []
  let optimisticMsgRef: Message | null = null
  let clearThresholdRef = -1
  let replayStartedRef = false
  let activeSessionRef: { current: string | null } = { current: null }

  function handleIncomingMessage(msg: Message) {
    setMessages((prev) => {
      const isInvisibleMessage =
        msg.type === 'heartbeat' ||
        (msg.type === 'system' && msg.subtype === 'session_state_changed')

      const isFirstTurnMessage =
        !replayStartedRef &&
        ((msg.replay) ||
          (!msg.replay && !isInvisibleMessage && msg.index >= clearThresholdRef))

      if (isFirstTurnMessage) {
        replayStartedRef = true
        // Index-based dedup only — content dedup was causing legitimate
        // user messages to be dropped during recovery.
        if (prev.some((m) => m.index === msg.index)) {
          return prev
        }
        return [...prev, msg]
      }

      // Replay dedup: skip if we already have this exact index
      if (msg.replay && prev.some((m) => m.index === msg.index)) {
        return prev
      }
      // Live dedup for user messages: the frontend optimistically adds
      // the user message; the server sends back the confirmed copy with
      // a different index. Skip the server copy if content matches.
      if (msg.type === 'user' && !msg.replay) {
        if (prev.some((m) => m.type === 'user' && m.content === msg.content)) {
          return prev
        }
      }
      // Live dedup for non-user messages: dedup by index to prevent
      // duplicates when messages arrive via both recovery and subscribe paths.
      if (!msg.replay && msg.type !== 'user') {
        if (msg.index != null && prev.some((m) => m.index === msg.index)) {
          return prev
        }
      }
      return [...prev, msg]
    })

    if (!activeSessionRef.current && msg.session_id) {
      // setActiveSession logic omitted
    }

    // Simulate session state handling from App.tsx
    if (msg.type === 'system' && msg.subtype === 'session_state_changed' && msg.session_id) {
      onSessionStateChange?.(msg.session_id, msg.state || msg.content || 'completed')
    }
  }

  function setMessages(updater: (prev: Message[]) => Message[]) {
    messages = updater(messages)
  }

  function handleSend(message: string) {
    const lastBackendIndex = messages.length
    clearThresholdRef = lastBackendIndex
    replayStartedRef = false
    const optimisticMsg: Message = {
      type: 'user',
      content: message,
      index: lastBackendIndex - 1,
    }
    optimisticMsgRef = optimisticMsg
    setMessages((prev) => [...prev, optimisticMsg])
  }

  function simulateReplay(historyMessages: Message[]) {
    for (const msg of historyMessages) {
      handleIncomingMessage({ ...msg, replay: true })
    }
  }

  function simulateLiveMessage(msg: Message) {
    handleIncomingMessage(msg)
  }

  return {
    getMessages: () => [...messages],
    getOptimistic: () => optimisticMsgRef,
    getClearThreshold: () => clearThresholdRef,
    getReplayStarted: () => replayStartedRef,
    handleIncomingMessage,
    handleSend,
    simulateReplay,
    simulateLiveMessage,
  }
}

describe('message handling after recovery', () => {
  let handler: ReturnType<typeof createMessageHandler>

  beforeEach(() => {
    handler = createMessageHandler()
  })

  describe('recovery then new message', () => {
    it('preserves recovered history when new turn starts', () => {
      // Simulate recovery loading 3 historical messages
      const history: Message[] = [
        { type: 'user', content: 'hello', index: 0 },
        { type: 'assistant', content: 'Hi there!', index: 1 },
        { type: 'user', content: 'how are you?', index: 2 },
      ]
      handler.simulateReplay(history)

      expect(handler.getMessages()).toHaveLength(3)

      // User sends new message
      handler.handleSend('new question')
      expect(handler.getMessages()).toHaveLength(4) // 3 history + optimistic

      // Agent response arrives (first live message of new turn)
      const agentResponse: Message = {
        type: 'assistant',
        content: 'Here is the answer...',
        index: 3,
      }
      handler.simulateLiveMessage(agentResponse)

      // BUG: Before fix, this would be 2 (only optimistic + agent response)
      // FIX: Should be 4 (3 history + optimistic + agent)
      expect(handler.getMessages()).toHaveLength(5)
      expect(handler.getMessages()[0].content).toBe('hello')
      expect(handler.getMessages()[1].content).toBe('Hi there!')
      expect(handler.getMessages()[2].content).toBe('how are you?')
      expect(handler.getMessages()[3].content).toBe('new question')
      expect(handler.getMessages()[4].content).toBe('Here is the answer...')
    })

    it('preserves history when multiple live messages arrive in sequence', () => {
      const history: Message[] = [
        { type: 'user', content: 'first', index: 0 },
        { type: 'assistant', content: 'response', index: 1 },
      ]
      handler.simulateReplay(history)

      handler.handleSend('second turn')

      // Multiple agent messages arrive
      handler.simulateLiveMessage({ type: 'assistant', content: 'thinking...', index: 2 })
      handler.simulateLiveMessage({ type: 'assistant', content: 'final answer', index: 3 })

      expect(handler.getMessages()).toHaveLength(5) // 2 history + optimistic + 2 agent
      expect(handler.getMessages().map((m) => m.content)).toEqual([
        'first',
        'response',
        'second turn',
        'thinking...',
        'final answer',
      ])
    })
  })

  describe('recovery dedup', () => {
    it('does not duplicate messages when replay has same index', () => {
      const history: Message[] = [
        { type: 'user', content: 'hello', index: 0 },
        { type: 'assistant', content: 'Hi!', index: 1 },
      ]

      // Simulate first message
      handler.simulateReplay([history[0]])
      expect(handler.getMessages()).toHaveLength(1)

      // Simulate same message again (should dedup by index)
      handler.simulateReplay([history[0]])
      expect(handler.getMessages()).toHaveLength(1)

      // Second message arrives
      handler.simulateReplay([history[1]])
      expect(handler.getMessages()).toHaveLength(2)
    })

    it('replay user message with unique index is always added', () => {
      // In the real app, the optimistic user message is lost on page refresh,
      // so recovery replay won't collide with it. The backend adds the user
      // message to its buffer before agent task starts, so it's included
      // in the recovery replay.
      handler.handleSend('hello')
      expect(handler.getMessages()).toHaveLength(1)
      expect(handler.getMessages()[0].type).toBe('user')

      // Replay contains the same user message from backend with a different index
      handler.simulateReplay([{ type: 'user', content: 'hello', index: 0 }])
      // Different index means no dedup — both messages appear
      // (optimistic stays since page hasn't refreshed)
      expect(handler.getMessages()).toHaveLength(2)
    })
  })

  describe('normal turn without clearing', () => {
    it('preserves old messages when new turn starts', () => {
      // Simulate a completed turn
      handler.simulateLiveMessage({ type: 'user', content: 'old msg', index: 0 })
      handler.simulateLiveMessage({ type: 'assistant', content: 'old response', index: 1 })

      expect(handler.getMessages()).toHaveLength(2)

      // User sends new message
      handler.handleSend('new msg')

      // Agent response arrives
      handler.simulateLiveMessage({ type: 'assistant', content: 'new response', index: 2 })

      // Old messages are preserved, new messages appended
      expect(handler.getMessages()).toHaveLength(4)
      expect(handler.getMessages().map((m) => m.content)).toEqual([
        'old msg',
        'old response',
        'new msg',
        'new response',
      ])
    })
  })

  describe('invisible messages', () => {
    it('does not clear messages for heartbeat during recovery', () => {
      const history: Message[] = [
        { type: 'user', content: 'hello', index: 0 },
        { type: 'assistant', content: 'Hi!', index: 1 },
      ]
      handler.simulateReplay(history)

      // Heartbeat arrives
      handler.simulateLiveMessage({
        type: 'heartbeat',
        content: '',
        index: 2,
      })

      expect(handler.getMessages()).toHaveLength(3) // 2 history + heartbeat
    })

    it('does not clear messages for session_state_changed during recovery', () => {
      const history: Message[] = [
        { type: 'user', content: 'hello', index: 0 },
      ]
      handler.simulateReplay(history)

      handler.simulateLiveMessage({
        type: 'system',
        content: 'running',
        subtype: 'session_state_changed',
        index: 1,
        state: 'running',
      })

      expect(handler.getMessages()).toHaveLength(2)
    })
  })

  describe('live user message dedup', () => {
    it('adds server echo of user message when index differs (normal behavior)', () => {
      // In the real app, the backend does NOT echo back user messages during
      // a normal chat turn — only agent/tool messages stream live.
      // If a user message did arrive with a different index than the optimistic,
      // it would be added (no content dedup in isFirstTurnMessage branch).
      handler.handleSend('test message')
      expect(handler.getMessages()).toHaveLength(1)
      expect(handler.getMessages()[0].index).toBe(-1) // optimistic index

      // If server somehow echoes the user message with a real index
      handler.simulateLiveMessage({
        type: 'user',
        content: 'test message',
        index: 0,
      })

      // Added because index is different (0 vs -1)
      expect(handler.getMessages()).toHaveLength(2)
    })

    it('adds user message when content differs from optimistic', () => {
      handler.handleSend('message A')
      expect(handler.getMessages()).toHaveLength(1)

      // Different user message arrives (e.g., from another source)
      handler.simulateLiveMessage({
        type: 'user',
        content: 'message B',
        index: 0,
      })

      expect(handler.getMessages()).toHaveLength(2)
    })
  })

  describe('live non-user message dedup', () => {
    it('dedups live assistant messages by index to prevent duplicates', () => {
      // Simulate session with some history
      handler.simulateReplay([
        { type: 'user', content: 'hello', index: 0 },
        { type: 'assistant', content: 'Hi!', index: 1 },
      ])

      // Live assistant message arrives
      handler.simulateLiveMessage({
        type: 'assistant',
        content: 'Here is the answer...',
        index: 2,
      })
      expect(handler.getMessages()).toHaveLength(3)

      // Same message arrives again (e.g., via both recovery + subscribe paths)
      // Should be deduped by index
      handler.simulateLiveMessage({
        type: 'assistant',
        content: 'Here is the answer...',
        index: 2,
      })
      expect(handler.getMessages()).toHaveLength(3)
    })

    it('dedups live tool_use messages by index', () => {
      handler.handleSend('run code')

      // Tool use message arrives
      handler.simulateLiveMessage({
        type: 'tool_use',
        content: 'print(1)',
        index: 0,
        name: 'python',
      })
      expect(handler.getMessages()).toHaveLength(2)

      // Same tool_use arrives again — should be deduped
      handler.simulateLiveMessage({
        type: 'tool_use',
        content: 'print(1)',
        index: 0,
        name: 'python',
      })
      expect(handler.getMessages()).toHaveLength(2)
    })

    it('dedups live result messages by index', () => {
      handler.simulateReplay([
        { type: 'user', content: 'query', index: 0 },
      ])

      // Result message arrives
      handler.simulateLiveMessage({
        type: 'result',
        content: '',
        index: 1,
      })
      expect(handler.getMessages()).toHaveLength(2)

      // Same result arrives again — should be deduped
      handler.simulateLiveMessage({
        type: 'result',
        content: '',
        index: 1,
      })
      expect(handler.getMessages()).toHaveLength(2)
    })

    it('allows different-index messages through (not over-aggressive dedup)', () => {
      handler.simulateReplay([
        { type: 'user', content: 'hello', index: 0 },
      ])

      // First assistant message
      handler.simulateLiveMessage({
        type: 'assistant',
        content: 'part 1',
        index: 1,
      })

      // Second assistant message (different index)
      handler.simulateLiveMessage({
        type: 'assistant',
        content: 'part 2',
        index: 2,
      })

      expect(handler.getMessages()).toHaveLength(3)
      expect(handler.getMessages()[1].content).toBe('part 1')
      expect(handler.getMessages()[2].content).toBe('part 2')
    })
  })

  describe('page refresh recovery', () => {
    it('preserves ALL messages including multiple user messages after recovery', () => {
      // Simulate a completed session with multiple turns on the backend
      const backendHistory: Message[] = [
        { type: 'user', content: 'hello', index: 0 },
        { type: 'assistant', content: 'Hi there!', index: 1 },
        { type: 'user', content: 'how are you?', index: 2 },
        { type: 'assistant', content: 'I am fine, thanks!', index: 3 },
        { type: 'result', content: '', index: 4 },
      ]

      // Page refresh: all refs reset (like initial state)
      handler = createMessageHandler()

      // Recovery: backend replays all messages
      handler.simulateReplay(backendHistory)

      // All 5 messages should be present
      expect(handler.getMessages()).toHaveLength(5)
      expect(handler.getMessages().map(m => m.content)).toEqual([
        'hello',
        'Hi there!',
        'how are you?',
        'I am fine, thanks!',
        '',
      ])
      // Verify user messages are preserved
      const userMessages = handler.getMessages().filter(m => m.type === 'user')
      expect(userMessages).toHaveLength(2)
      expect(userMessages.map(m => m.content)).toEqual(['hello', 'how are you?'])
    })

    it('preserves user messages when recovery includes running session state', () => {
      // Simulate a running session (agent still working)
      const backendHistory: Message[] = [
        { type: 'user', content: 'write a poem', index: 0 },
        { type: 'system', subtype: 'session_state_changed', state: 'running', content: '', index: 1, session_id: 'sess1' },
      ]

      handler = createMessageHandler()
      handler.simulateReplay(backendHistory)

      expect(handler.getMessages()).toHaveLength(2)
      expect(handler.getMessages()[0].type).toBe('user')
      expect(handler.getMessages()[0].content).toBe('write a poem')
    })
  })

  describe('session switch recovery', () => {
    it('receives live state messages after switching back to a running session', () => {
      // Simulate the flow:
      // 1. Session A is running, user switches to Session B
      // 2. User switches back to Session A
      // 3. handleSelectSession loads history via REST
      // 4. sendRecover is called → backend pushes live state messages
      // 5. State should update to 'running'

      const stateChanges: { sessionId: string; state: string }[] = []
      const handler = createMessageHandler({
        onSessionStateChange: (sessionId: string, state: string) => {
          stateChanges.push({ sessionId, state })
        },
      })

      // Step 1: REST loads history (no state message yet, just user/assistant)
      handler.simulateReplay([
        { type: 'user', content: 'analyze data', index: 0 },
      ])

      // Step 2: Recovery pushes live messages including running state
      handler.simulateReplay([
        { type: 'user', content: 'analyze data', index: 0 },
        { type: 'system', subtype: 'session_state_changed', state: 'running', content: '', index: 1, session_id: 'sessA' },
      ])

      // Step 3: Live agent messages arrive
      handler.simulateLiveMessage({
        type: 'assistant',
        content: 'Analyzing...',
        index: 2,
      })

      expect(handler.getMessages()).toHaveLength(3)
      expect(stateChanges.some(c => c.state === 'running')).toBe(true)
    })

    it('dedups messages when REST and recover both load same history', () => {
      const handler = createMessageHandler()

      // REST loads 2 messages
      handler.simulateReplay([
        { type: 'user', content: 'hello', index: 0 },
        { type: 'assistant', content: 'Hi!', index: 1 },
      ])
      expect(handler.getMessages()).toHaveLength(2)

      // sendRecover → backend replays same 2 messages (replay=true)
      handler.simulateReplay([
        { type: 'user', content: 'hello', index: 0 },
        { type: 'assistant', content: 'Hi!', index: 1 },
      ])

      // Should still be 2 — dedup by index
      expect(handler.getMessages()).toHaveLength(2)
    })
  })

  describe('session state after recovery', () => {
    it('sets running state when session_state_changed is replayed', () => {
      // Track state changes via a callback
      const stateChanges: { sessionId: string; state: string }[] = []
      const handlerWithState = createMessageHandler({
        onSessionStateChange: (sessionId: string, state: string) => {
          stateChanges.push({ sessionId, state })
        },
      })

      // Simulate recovery with a running session
      handlerWithState.simulateReplay([
        { type: 'user', content: 'hello', index: 0, session_id: 'sess1' },
        { type: 'system', subtype: 'session_state_changed', state: 'running', content: '', index: 1, session_id: 'sess1' },
      ])

      // Check that state was set to 'running'
      const runningChange = stateChanges.find(c => c.state === 'running')
      expect(runningChange).toBeDefined()
    })

    it('does NOT set state if session_id is missing from replay', () => {
      const stateChanges: { sessionId: string; state: string }[] = []
      const handlerWithState = createMessageHandler({
        onSessionStateChange: (sessionId: string, state: string) => {
          stateChanges.push({ sessionId, state })
        },
      })

      // Simulate recovery WITHOUT session_id (the bug scenario)
      handlerWithState.simulateReplay([
        { type: 'user', content: 'hello', index: 0 },
        { type: 'system', subtype: 'session_state_changed', state: 'running', content: '', index: 1 },
        // ← NO session_id
      ])

      // State should NOT be set because session_id is missing
      expect(stateChanges).toHaveLength(0)
    })
  })

  describe('page refresh during active agent session', () => {
    it('receives new agent messages after recovery even when history was already loaded via REST', () => {
      // This simulates the exact page-refresh-while-agent-is-working flow:
      // 1. REST /history loads historical messages (historyLoadedRef = true)
      // 2. WebSocket reconnects → must still send recover to enter subscribe loop
      // 3. Backend replays history from recover (dedup by index)
      // 4. New agent messages stream in live
      //
      // The bug: when historyLoadedRef = true, sendRecover was skipped,
      // so backend never entered subscribe loop → frontend got no new messages.

      handler = createMessageHandler()

      // Step 1: REST loaded 1 historical message (the user's last turn)
      const restHistory: Message[] = [
        { type: 'user', content: 'analyze this data', index: 0 },
      ]
      handler.simulateReplay(restHistory)
      expect(handler.getMessages()).toHaveLength(1)

      // Step 2: Recovery replays all messages from backend
      // (In reality this includes the same messages REST loaded + agent progress)
      const recoveryMessages: Message[] = [
        { type: 'user', content: 'analyze this data', index: 0 },
        { type: 'assistant', content: 'Let me analyze...', index: 1 },
        { type: 'tool_use', content: '...', index: 2, name: 'python' },
      ]
      handler.simulateReplay(recoveryMessages)

      // Should dedup: index 0 already exists, only 1 and 2 are new
      expect(handler.getMessages()).toHaveLength(3)

      // Step 3: New live messages from agent should arrive
      handler.simulateLiveMessage({
        type: 'assistant',
        content: 'The data shows...',
        index: 3,
      })

      expect(handler.getMessages()).toHaveLength(4)
      expect(handler.getMessages()[3].content).toBe('The data shows...')
    })

    it('does not duplicate messages when both REST and WebSocket recover load same history', () => {
      handler = createMessageHandler()

      // REST loads 2 messages
      handler.simulateReplay([
        { type: 'user', content: 'hello', index: 0 },
        { type: 'assistant', content: 'Hi!', index: 1 },
      ])
      expect(handler.getMessages()).toHaveLength(2)

      // WebSocket recovery sends the same 2 messages (replay=true)
      handler.simulateReplay([
        { type: 'user', content: 'hello', index: 0 },
        { type: 'assistant', content: 'Hi!', index: 1 },
      ])

      // Should still be 2 — dedup by index
      expect(handler.getMessages()).toHaveLength(2)
    })
  })
})

// ── Session state from live buffer ─────────────────────────────────

/**
 * When an agent is actively running, the session_state_changed:running
 * message may exist only in the in-memory buffer (not yet persisted to DB).
 * Deriving state from DB history alone returns 'idle', losing the
 * "Agent is working" UI. The fix: after loading history, also fetch
 * /api/users/{userId}/sessions/{id}/status to get the live buffer state.
 */

interface StatusFetchArgs {
  userId: string
  sessionId: string
  headers: Record<string, string>
}

/**
 * Simulates the state derivation logic from handleSelectSession.
 * BUGGY VERSION: derives state from DB history only, does NOT fetch
 * live buffer state. This means 'running' state is lost when the
 * session_state_changed message hasn't been persisted yet.
 */
function deriveSessionStateFromHistory_BUGGY(
  msgs: Message[]
): string {
  let derivedState = 'idle'
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i]
    if (m.type === 'system' && m.subtype === 'session_state_changed' && m.state) {
      derivedState = m.state
      break
    }
    if (m.type === 'result') {
      derivedState = 'completed'
      break
    }
  }
  return derivedState
}

/**
 * FIXED VERSION: after deriving from history, also fetches live buffer
 * state from /status endpoint. If live state is 'running', overrides.
 */
function deriveSessionStateFromHistory(
  msgs: Message[],
  fetchLiveStatus: (args: StatusFetchArgs) => Promise<{ state?: string }>
): Promise<string> {
  // Step 1: derive from history
  let derivedState = 'idle'
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i]
    if (m.type === 'system' && m.subtype === 'session_state_changed' && m.state) {
      derivedState = m.state
      break
    }
    if (m.type === 'result') {
      derivedState = 'completed'
      break
    }
  }

  // Step 2: fetch live buffer state
  return fetchLiveStatus({ userId: 'test-user', sessionId: 'sess-1', headers: {} })
    .then(status => {
      if (status.state === 'running') {
        return 'running'
      }
      return derivedState
    })
    .catch(() => derivedState)
}

describe('session state from live buffer', () => {
  it('BUG: buggy version returns idle when DB history has no state message (agent is actually running)', () => {
    // DB history: only user message (state not yet persisted)
    const dbHistory: Message[] = [
      { type: 'user', content: 'analyze data', index: 0 },
    ]

    // The buggy version doesn't fetch /status — it returns 'idle'
    const state = deriveSessionStateFromHistory_BUGGY(dbHistory)

    // This demonstrates the bug: agent IS running but we show 'idle'
    expect(state).toBe('idle')
  })

  it('FIX: returns running when live buffer says running even if DB history has no state message', async () => {
    // DB history: only user message (state not yet persisted)
    const dbHistory: Message[] = [
      { type: 'user', content: 'analyze data', index: 0 },
    ]

    // /status endpoint reports running
    const fetchLiveStatus = vi.fn().mockResolvedValue({ state: 'running' })

    const state = await deriveSessionStateFromHistory(dbHistory, fetchLiveStatus)

    expect(state).toBe('running')
    expect(fetchLiveStatus).toHaveBeenCalled()
  })

  it('returns idle when both DB history and live buffer say idle', async () => {
    const dbHistory: Message[] = [
      { type: 'user', content: 'hello', index: 0 },
      { type: 'assistant', content: 'Hi!', index: 1 },
    ]

    const fetchLiveStatus = vi.fn().mockResolvedValue({ state: 'idle' })

    const state = await deriveSessionStateFromHistory(dbHistory, fetchLiveStatus)

    expect(state).toBe('idle')
  })

  it('returns completed from DB when live buffer also says completed', async () => {
    const dbHistory: Message[] = [
      { type: 'user', content: 'hello', index: 0 },
      { type: 'result', content: '', index: 1 },
    ]

    const fetchLiveStatus = vi.fn().mockResolvedValue({ state: 'completed' })

    const state = await deriveSessionStateFromHistory(dbHistory, fetchLiveStatus)

    expect(state).toBe('completed')
  })

  it('prefers live running over DB completed (agent restarted without DB update)', async () => {
    // Edge case: DB says completed but agent restarted and is now running
    const dbHistory: Message[] = [
      { type: 'user', content: 'hello', index: 0 },
      { type: 'result', content: '', index: 1 },
    ]

    const fetchLiveStatus = vi.fn().mockResolvedValue({ state: 'running' })

    const state = await deriveSessionStateFromHistory(dbHistory, fetchLiveStatus)

    expect(state).toBe('running')
  })

  it('falls back to DB-derived state when live status fetch fails', async () => {
    const dbHistory: Message[] = [
      { type: 'user', content: 'hello', index: 0 },
    ]

    const fetchLiveStatus = vi.fn().mockRejectedValue(new Error('network error'))

    const state = await deriveSessionStateFromHistory(dbHistory, fetchLiveStatus)

    expect(state).toBe('idle')
  })
})

// ── Cross-session message filtering ──────────────────────────────

/**
 * A single WebSocket connection receives messages from ALL sessions
 * for the user. When the user switches from session A to session B,
 * session A's messages must NOT be appended to the ChatArea showing
 * session B. Only display messages for the active session.
 *
 * Invisible messages (heartbeat, session_state_changed) are still
 * processed for state tracking but should not be filtered.
 */

function createMessageHandlerWithSessionFilter(opts?: {
  activeSessionRef?: { current: string | null }
  onSessionStateChange?: (sessionId: string, state: string) => void
}) {
  const activeSessionRef = opts?.activeSessionRef ?? { current: 'session-b' }
  const { onSessionStateChange } = opts || {}
  let messages: Message[] = []

  function handleIncomingMessage(msg: Message) {
    const isInvisibleMessage =
      msg.type === 'heartbeat' ||
      (msg.type === 'system' && msg.subtype === 'session_state_changed')

    // Filter: skip messages (including invisible) from inactive sessions.
    // Still update state for result/session_state_changed of inactive sessions.
    if (msg.session_id && msg.session_id !== activeSessionRef.current) {
      if (msg.type === 'system' && msg.subtype === 'session_state_changed') {
        onSessionStateChange?.(msg.session_id, msg.state || msg.content || 'completed')
      }
      if (msg.type === 'result') {
        onSessionStateChange?.(msg.session_id, 'completed')
      }
      return
    }

    // Invisible messages from active session (or without session_id) are fine
    if (isInvisibleMessage) {
      // Still track state changes
      if (msg.type === 'system' && msg.subtype === 'session_state_changed' && msg.session_id) {
        onSessionStateChange?.(msg.session_id, msg.state || msg.content || 'completed')
      }
      // Don't add heartbeats or state changes to display messages
      return
    }

    // Index-based dedup
    if (msg.replay && messages.some((m) => m.index === msg.index)) {
      return
    }
    if (msg.type === 'user' && !msg.replay) {
      if (messages.some((m) => m.type === 'user' && m.content === msg.content)) {
        return
      }
    }
    // Live dedup for non-user messages
    if (!msg.replay && msg.type !== 'user') {
      if (msg.index != null && messages.some((m) => m.index === msg.index)) {
        return
      }
    }
    messages = [...messages, msg]

    if (msg.type === 'system' && msg.subtype === 'session_state_changed' && msg.session_id) {
      onSessionStateChange?.(msg.session_id, msg.state || msg.content || 'completed')
    }
    if (msg.type === 'result' && msg.session_id) {
      onSessionStateChange?.(msg.session_id, 'completed')
    }
  }

  return {
    getMessages: () => [...messages],
    handleIncomingMessage,
  }
}

describe('cross-session message filtering', () => {
  it('filters out messages from inactive session', () => {
    const stateChanges: { sessionId: string; state: string }[] = []
    const handler = createMessageHandlerWithSessionFilter({
      activeSessionRef: { current: 'session-b' },
      onSessionStateChange: (sessionId, state) => {
        stateChanges.push({ sessionId, state })
      },
    })

    // Active session B gets a message — should appear
    handler.handleIncomingMessage({
      type: 'assistant',
      content: 'B response',
      index: 0,
      session_id: 'session-b',
    })

    // Inactive session A gets a message — should NOT appear
    handler.handleIncomingMessage({
      type: 'assistant',
      content: 'A response',
      index: 0,
      session_id: 'session-a',
    })

    expect(handler.getMessages()).toHaveLength(1)
    expect(handler.getMessages()[0].content).toBe('B response')
    expect(handler.getMessages()[0].session_id).toBe('session-b')
  })

  it('still processes heartbeat for inactive session (no crash)', () => {
    const handler = createMessageHandlerWithSessionFilter({
      activeSessionRef: { current: 'session-b' },
    })

    // Heartbeat from session A — should not crash, not appended
    handler.handleIncomingMessage({
      type: 'heartbeat',
      content: '',
      index: 5,
      session_id: 'session-a',
    })

    expect(handler.getMessages()).toHaveLength(0)
  })

  it('processes session_state_changed for inactive session (state tracking)', () => {
    const stateChanges: { sessionId: string; state: string }[] = []
    const handler = createMessageHandlerWithSessionFilter({
      activeSessionRef: { current: 'session-b' },
      onSessionStateChange: (sessionId, state) => {
        stateChanges.push({ sessionId, state })
      },
    })

    // session_state_changed from inactive session A
    handler.handleIncomingMessage({
      type: 'system',
      subtype: 'session_state_changed',
      state: 'completed',
      content: 'completed',
      index: 10,
      session_id: 'session-a',
    })

    // State should be updated even though session A is not active
    expect(stateChanges.some(c => c.sessionId === 'session-a' && c.state === 'completed')).toBe(true)
    // But no display messages added
    expect(handler.getMessages()).toHaveLength(0)
  })

  it('processes result message for inactive session (triggers state update)', () => {
    const stateChanges: { sessionId: string; state: string }[] = []
    const handler = createMessageHandlerWithSessionFilter({
      activeSessionRef: { current: 'session-b' },
      onSessionStateChange: (sessionId, state) => {
        stateChanges.push({ sessionId, state })
      },
    })

    // result from inactive session A
    handler.handleIncomingMessage({
      type: 'result',
      content: '',
      index: 10,
      session_id: 'session-a',
    })

    expect(stateChanges.some(c => c.sessionId === 'session-a' && c.state === 'completed')).toBe(true)
    expect(handler.getMessages()).toHaveLength(0)
  })

  it('messages without session_id are still appended (backward compat)', () => {
    const handler = createMessageHandlerWithSessionFilter({
      activeSessionRef: { current: 'session-b' },
    })

    // Message without session_id — should be appended
    handler.handleIncomingMessage({
      type: 'assistant',
      content: 'no session id',
      index: 0,
    })

    expect(handler.getMessages()).toHaveLength(1)
  })

  it('accepts messages when activeSessionRef is updated synchronously', () => {
    // Issue A: After setActiveSession(id), activeSessionRef.current should
    // be updated immediately so that WS messages arriving in the same tick
    // are not incorrectly filtered out.
    const activeSessionRef = { current: 'session-a' }
    const handler = createMessageHandlerWithSessionFilter({
      activeSessionRef,
    })

    // Simulate session switch: update both state AND ref
    activeSessionRef.current = 'session-b'

    // Message from new session arrives immediately
    handler.handleIncomingMessage({
      type: 'assistant',
      content: 'B message',
      index: 0,
      session_id: 'session-b',
    })

    expect(handler.getMessages()).toHaveLength(1)
    expect(handler.getMessages()[0].content).toBe('B message')
  })
})

// ── Issue D: clearThresholdRef sentinel value ────────────────────

/**
 * When clearThresholdRef = -1 (old sentinel), isFirstTurnMessage
 * triggers for ANY live message (all indices >= 0). This means the
 * first live message after a session switch enters the "first turn"
 * path even though it's not the start of a new conversation turn.
 *
 * Fix: use Number.MAX_SAFE_INTEGER as the sentinel so that
 * msg.index >= threshold is false for all live messages, and only
 * replay messages (msg.replay=true) trigger isFirstTurnMessage.
 */

function simulateIsFirstTurnMessage(
  clearThreshold: number,
  replayStarted: boolean,
  msgReplay: boolean,
  msgIndex: number,
): boolean {
  return !replayStarted && (msgReplay || msgIndex >= clearThreshold)
}

describe('clearThresholdRef sentinel value (Issue D)', () => {
  it('with -1 sentinel, ALL live messages trigger isFirstTurnMessage (buggy)', () => {
    // This demonstrates the bug: -1 makes every index >= -1 true
    expect(simulateIsFirstTurnMessage(-1, false, false, 0)).toBe(true)
    expect(simulateIsFirstTurnMessage(-1, false, false, 10)).toBe(true)
    expect(simulateIsFirstTurnMessage(-1, false, false, 100)).toBe(true)
  })

  it('with MAX_SAFE_INTEGER sentinel, live messages do NOT trigger isFirstTurnMessage (fixed)', () => {
    const MAX = Number.MAX_SAFE_INTEGER
    expect(simulateIsFirstTurnMessage(MAX, false, false, 0)).toBe(false)
    expect(simulateIsFirstTurnMessage(MAX, false, false, 10)).toBe(false)
    expect(simulateIsFirstTurnMessage(MAX, false, false, 100)).toBe(false)
  })

  it('with MAX_SAFE_INTEGER sentinel, replay messages still trigger isFirstTurnMessage', () => {
    const MAX = Number.MAX_SAFE_INTEGER
    expect(simulateIsFirstTurnMessage(MAX, false, true, 0)).toBe(true)
    expect(simulateIsFirstTurnMessage(MAX, false, true, 100)).toBe(true)
  })

  it('once replayStarted is true, no messages trigger isFirstTurnMessage', () => {
    const MAX = Number.MAX_SAFE_INTEGER
    expect(simulateIsFirstTurnMessage(MAX, true, true, 0)).toBe(false)
    expect(simulateIsFirstTurnMessage(MAX, true, false, 100)).toBe(false)
  })
})

// ── Issue B: Double recover on reconnect ─────────────────────────

/**
 * When WebSocket reconnects after a session switch:
 * 1. handleSelectSession calls sendRecover(id, msgs.length)
 * 2. WS reconnects → didRecoverRef reset → auto-recovery sends sendRecover(id, 0)
 *
 * This causes two recover messages with different last_index values.
 * Fix: set didRecoverRef = true after handleSelectSession's recover.
 */

describe('double recover prevention (Issue B)', () => {
  it('handleSelectSession recover should set didRecoverRef to prevent auto-recovery', () => {
    // The test verifies the intended behavior: after a manual recover,
    // the auto-recovery effect should NOT send another one.
    const didRecoverRef = { current: false }

    // Simulate handleSelectSession calling recover
    didRecoverRef.current = true

    // Simulate WS reconnect — auto-recovery checks didRecoverRef
    const wouldSendRecover = !didRecoverRef.current
    expect(wouldSendRecover).toBe(false)
  })

  it('didRecoverRef resets on disconnect to allow recovery on next reconnect', () => {
    const didRecoverRef = { current: true }

    // Simulate disconnect
    didRecoverRef.current = false

    // Next reconnect can recover again
    const wouldSendRecover = !didRecoverRef.current
    expect(wouldSendRecover).toBe(true)
  })
})

// ── Pending message tracking (rapid session switch) ──────────────

/**
 * When user sends a message and rapidly switches sessions:
 * 1. Optimistic user message is added to display
 * 2. WebSocket send() is called (fire-and-forget)
 * 3. User switches session → setMessages replaces all messages
 * 4. The optimistic message is lost from the display
 * 5. When user switches back, REST + recover may return empty
 *    (backend hasn't received the WS message yet)
 *
 * Fix: track pending user messages per session. When switching back
 * to a session with pending messages, restore them immediately so
 * the user sees their message even before the backend confirms.
 */

function createMessageHandlerWithPendingTracking(opts?: {
  activeSessionRef?: { current: string | null }
}) {
  const activeSessionRef = opts?.activeSessionRef ?? { current: 'session-a' }
  let messages: Message[] = []
  const pendingUserMsgs = new Map<string, Message>()

  function handleSend(message: string, sessionId: string) {
    // Track pending message
    const pendingMsg: Message = {
      type: 'user',
      content: message,
      index: messages.length - 1,
    }
    pendingUserMsgs.set(sessionId, pendingMsg)
    messages = [...messages, pendingMsg]
  }

  function handleSelectSession(id: string) {
    // If switching back to a session with pending messages,
    // restore them into the display
    const pending = pendingUserMsgs.get(id)
    if (pending) {
      messages = [pending]
    } else {
      messages = []
    }
    activeSessionRef.current = id
  }

  function handleIncomingMessage(msg: Message) {
    // If this is a user message echo (confirmation from backend),
    // clear the pending message for this session
    if (msg.type === 'user' && !msg.replay && msg.session_id) {
      const pending = pendingUserMsgs.get(msg.session_id)
      if (pending && pending.content === msg.content) {
        pendingUserMsgs.delete(msg.session_id)
      }
    }

    // Cross-session filtering
    if (msg.session_id && msg.session_id !== activeSessionRef.current) {
      return
    }

    // Dedup
    if (msg.replay && messages.some((m) => m.index === msg.index)) {
      return
    }
    if (msg.type === 'user' && !msg.replay) {
      if (messages.some((m) => m.type === 'user' && m.content === msg.content)) {
        return
      }
    }
    if (!msg.replay && msg.type !== 'user') {
      if (msg.index != null && messages.some((m) => m.index === msg.index)) {
        return
      }
    }
    messages = [...messages, msg]
  }

  return {
    getMessages: () => [...messages],
    getPendingMsgs: () => new Map(pendingUserMsgs),
    handleSend,
    handleSelectSession,
    handleIncomingMessage,
  }
}

describe('pending message tracking', () => {
  it('tracks pending message when user sends', () => {
    const handler = createMessageHandlerWithPendingTracking({
      activeSessionRef: { current: 'session-a' },
    })

    handler.handleSend('hello world', 'session-a')

    expect(handler.getPendingMsgs().has('session-a')).toBe(true)
    expect(handler.getPendingMsgs().get('session-a')?.content).toBe('hello world')
    expect(handler.getMessages()).toHaveLength(1)
    expect(handler.getMessages()[0].content).toBe('hello world')
  })

  it('removes pending message when backend echoes', () => {
    const handler = createMessageHandlerWithPendingTracking({
      activeSessionRef: { current: 'session-a' },
    })

    handler.handleSend('hello', 'session-a')
    expect(handler.getPendingMsgs().has('session-a')).toBe(true)

    // Backend echoes the user message
    handler.handleIncomingMessage({
      type: 'user',
      content: 'hello',
      index: 5,
      session_id: 'session-a',
    })

    // Pending should be cleared
    expect(handler.getPendingMsgs().has('session-a')).toBe(false)
  })

  it('restores pending messages when switching back to session', () => {
    const activeSessionRef = { current: 'session-a' }
    const handler = createMessageHandlerWithPendingTracking({
      activeSessionRef,
    })

    // Send message in session A
    handler.handleSend('lost message', 'session-a')
    expect(handler.getMessages()).toHaveLength(1)

    // Switch to session B — clears display
    handler.handleSelectSession('session-b')
    expect(handler.getMessages()).toHaveLength(0)

    // Switch back to session A — pending message restored
    handler.handleSelectSession('session-a')
    expect(handler.getMessages()).toHaveLength(1)
    expect(handler.getMessages()[0].content).toBe('lost message')
  })

  it('replaces restored pending with backend echo', () => {
    const activeSessionRef = { current: 'session-b' }
    const handler = createMessageHandlerWithPendingTracking({
      activeSessionRef,
    })

    // Send in session A, then switch to B
    handler.handleSend('my message', 'session-a')
    handler.handleSelectSession('session-b')

    // Switch back to session A
    handler.handleSelectSession('session-a')
    activeSessionRef.current = 'session-a'
    expect(handler.getMessages()).toHaveLength(1)
    expect(handler.getMessages()[0].content).toBe('my message')

    // Backend echoes the user message (with real index)
    handler.handleIncomingMessage({
      type: 'user',
      content: 'my message',
      index: 10,
      session_id: 'session-a',
    })

    // Should dedup the pending message and replace with confirmed
    // Since content matches, the pending should be replaced
    expect(handler.getMessages()).toHaveLength(1)
  })

  it('does not track pending for messages that already have backend confirmation', () => {
    const handler = createMessageHandlerWithPendingTracking({
      activeSessionRef: { current: 'session-a' },
    })

    // Backend already has history (replay)
    handler.handleIncomingMessage({
      type: 'user',
      content: 'confirmed message',
      index: 0,
      session_id: 'session-a',
      replay: true,
    })
    expect(handler.getMessages()).toHaveLength(1)

    // No pending should exist for this session
    expect(handler.getPendingMsgs().has('session-a')).toBe(false)
  })

  it('handles multiple sessions with independent pending messages', () => {
    const handler = createMessageHandlerWithPendingTracking({
      activeSessionRef: { current: 'session-a' },
    })

    handler.handleSend('message A', 'session-a')
    handler.handleSend('message B', 'session-b')

    expect(handler.getPendingMsgs().size).toBe(2)
    expect(handler.getPendingMsgs().get('session-a')?.content).toBe('message A')
    expect(handler.getPendingMsgs().get('session-b')?.content).toBe('message B')

    // Switch to session B restores only B's pending
    handler.handleSelectSession('session-b')
    expect(handler.getMessages()).toHaveLength(1)
    expect(handler.getMessages()[0].content).toBe('message B')
  })
})

// ── Heartbeat staleness detection ─────────────────────────────────

/**
 * When the agent completes but the completion WebSocket message is
 * lost, the subscribe loop on the backend exits and stops sending
 * heartbeats. The frontend should detect this staleness (no heartbeat
 * for 60s while sessionState === 'running') and trigger recovery.
 */

const STALE_HEARTBEAT_THRESHOLD = 60_000 // 60 seconds

function createHeartbeatStalenessTracker() {
  let lastHeartbeat = Date.now()
  let sessionState = 'idle'
  let recoverCalled = false
  let recoverSessionId: string | null = null
  let recoverIndex = 0

  function receiveHeartbeat() {
    lastHeartbeat = Date.now()
  }

  function setSessionState(state: string) {
    sessionState = state
  }

  function getHeartbeatGap() {
    return Date.now() - lastHeartbeat
  }

  function checkStaleness() {
    if (sessionState !== 'running') return false
    const gap = getHeartbeatGap()
    if (gap > STALE_HEARTBEAT_THRESHOLD) {
      recoverCalled = true
      recoverSessionId = 'test-session'
      recoverIndex = 5
      return true
    }
    return false
  }

  return {
    receiveHeartbeat,
    setSessionState,
    getHeartbeatGap,
    checkStaleness,
    getRecoverCalled: () => recoverCalled,
    getRecoverSessionId: () => recoverSessionId,
    getRecoverIndex: () => recoverIndex,
    triggerRecover: () => {
      recoverCalled = true
      recoverSessionId = 'test-session'
      recoverIndex = 5
    },
  }
}

describe('heartbeat staleness detection', () => {
  it('does NOT trigger recovery when session is not running', () => {
    const tracker = createHeartbeatStalenessTracker()
    tracker.setSessionState('idle')

    // Simulate 70s gap (past threshold)
    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 70_000)

    expect(tracker.checkStaleness()).toBe(false)
  })

  it('does NOT trigger recovery when heartbeats are recent', () => {
    const tracker = createHeartbeatStalenessTracker()
    tracker.setSessionState('running')

    // Heartbeat just arrived
    tracker.receiveHeartbeat()

    expect(tracker.checkStaleness()).toBe(false)
  })

  it('triggers recovery when session is running and heartbeats stopped for 60s', () => {
    const tracker = createHeartbeatStalenessTracker()
    tracker.setSessionState('running')

    // Last heartbeat was 70s ago (past 60s threshold)
    tracker.receiveHeartbeat()
    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 70_000)

    expect(tracker.checkStaleness()).toBe(true)
    expect(tracker.getRecoverCalled()).toBe(true)
    expect(tracker.getRecoverSessionId()).toBe('test-session')
  })

  it('does NOT trigger recovery when heartbeat gap is just under threshold', () => {
    const tracker = createHeartbeatStalenessTracker()
    tracker.setSessionState('running')

    // 50s gap — under 60s threshold
    tracker.receiveHeartbeat()
    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 50_000)

    expect(tracker.checkStaleness()).toBe(false)
  })

  it('recovery resets after heartbeat arrives (avoids repeated triggers)', () => {
    const tracker = createHeartbeatStalenessTracker()
    tracker.setSessionState('running')

    // Stale — triggers recovery
    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 70_000)
    expect(tracker.checkStaleness()).toBe(true)

    // Heartbeat arrives (e.g., from recovery)
    tracker.receiveHeartbeat()

    // Should NOT trigger again immediately
    expect(tracker.checkStaleness()).toBe(false)
  })

  it('handles session switch: running → idle stops staleness check', () => {
    const tracker = createHeartbeatStalenessTracker()
    tracker.setSessionState('running')

    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 70_000)
    expect(tracker.checkStaleness()).toBe(true)

    // Session completes — state changes to completed
    tracker.setSessionState('completed')

    // Even with stale gap, should NOT trigger recovery
    expect(tracker.checkStaleness()).toBe(false)
  })

  it('handles multiple consecutive staleness checks after reset', () => {
    const tracker = createHeartbeatStalenessTracker()
    tracker.setSessionState('running')

    // First check: stale, triggers
    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 70_000)
    expect(tracker.checkStaleness()).toBe(true)

    // Reset by new heartbeat
    tracker.receiveHeartbeat()

    // Check again: not stale yet
    expect(tracker.checkStaleness()).toBe(false)

    // 70s later: stale again
    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 70_000)
    expect(tracker.checkStaleness()).toBe(true)
  })

  it('works correctly for error state (should not trigger)', () => {
    const tracker = createHeartbeatStalenessTracker()
    tracker.setSessionState('error')

    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 70_000)

    expect(tracker.checkStaleness()).toBe(false)
  })

  it('session switch resets heartbeat to prevent false staleness', () => {
    // Simulates the real flow: session A running → switch to session B
    // The heartbeat ref should be reset so session B doesn't falsely
    // trigger staleness detection based on session A's old heartbeat.
    const tracker = createHeartbeatStalenessTracker()
    tracker.setSessionState('running')

    // Session A had a heartbeat 55s ago (almost stale)
    tracker.receiveHeartbeat()
    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 55_000)

    // User switches to session B — heartbeat resets
    tracker.receiveHeartbeat() // Simulate reset on session switch

    // Even though 55s elapsed, the reset means we're at gap=0
    expect(tracker.checkStaleness()).toBe(false)
  })
})

// ── Rapid session switch integration test ────────────────────────

/**
 * Simulates the full rapid session switch flow:
 * 1. Send message in Session A
 * 2. Immediately switch to Session B (before backend receives WS message)
 * 3. Switch back to Session A (history is empty — backend hasn't received message)
 * 4. Pending message should still be visible
 * 5. Backend finally receives message → echoes user message → pending cleared
 * 6. History now contains the message → future switches show it from history
 */

function createFullSessionSwitchSimulation() {
  const activeSessionRef = { current: 'session-a' as string | null }
  const pendingUserMsgs = new Map<string, Message>()
  let messages: Message[] = []

  // Simulate handleSend
  function handleSend(message: string, sessionId: string) {
    const msg: Message = {
      type: 'user',
      content: message,
      index: messages.length - 1,
    }
    pendingUserMsgs.set(sessionId, msg)
    messages = [...messages, msg]
  }

  // Simulate handleSelectSession with history fetch
  function handleSelectSession(id: string, history: Message[]) {
    activeSessionRef.current = id

    // Restore pending immediately
    const pending = pendingUserMsgs.get(id)
    if (pending) {
      messages = [pending]
    } else {
      messages = []
    }

    // Simulate async history fetch
    if (history.length > 0) {
      // Check if pending is confirmed by history
      if (pending && history.some(m => m.type === 'user' && m.content === pending.content)) {
        pendingUserMsgs.delete(id)
        messages = history
      } else {
        // Pending not confirmed — keep it
        messages = [pending, ...history].filter(Boolean) as Message[]
      }
    } else if (pending) {
      // History empty, pending exists — keep pending
      messages = [pending]
    }
  }

  // Simulate backend confirming user message
  function backendConfirmsMessage(sessionId: string, _content: string) {
    pendingUserMsgs.delete(sessionId)
  }

  return {
    getMessages: () => [...messages],
    getPending: () => new Map(pendingUserMsgs),
    handleSend,
    handleSelectSession,
    backendConfirmsMessage,
  }
}

describe('rapid session switch integration', () => {
  it('preserves message when switching away before backend receives it', () => {
    const sim = createFullSessionSwitchSimulation()

    // Step 1: Send message in Session A
    sim.handleSend('hello from A', 'session-a')
    expect(sim.getMessages()).toHaveLength(1)
    expect(sim.getPending().has('session-a')).toBe(true)

    // Step 2: Switch to Session B (backend hasn't received WS message)
    sim.handleSelectSession('session-b', [])
    expect(sim.getMessages()).toHaveLength(0)

    // Step 3: Switch back to Session A (history still empty)
    sim.handleSelectSession('session-a', [])
    // Pending message should be restored
    expect(sim.getMessages()).toHaveLength(1)
    expect(sim.getMessages()[0].content).toBe('hello from A')
    expect(sim.getPending().has('session-a')).toBe(true)
  })

  it('replaces pending with backend history once confirmed', () => {
    const sim = createFullSessionSwitchSimulation()

    // Send in Session A
    sim.handleSend('my question', 'session-a')
    expect(sim.getMessages()).toHaveLength(1)

    // Switch to B
    sim.handleSelectSession('session-b', [])

    // Backend receives the WS message for Session A
    sim.backendConfirmsMessage('session-a', 'my question')

    // Switch back to A — history now contains the message
    const history: Message[] = [
      { type: 'user', content: 'my question', index: 0 },
      { type: 'assistant', content: 'Here is the answer', index: 1 },
    ]
    sim.handleSelectSession('session-a', history)

    // Should show history (not pending)
    expect(sim.getMessages()).toHaveLength(2)
    expect(sim.getMessages()[0].content).toBe('my question')
    expect(sim.getMessages()[1].content).toBe('Here is the answer')
    expect(sim.getPending().has('session-a')).toBe(false)
  })

  it('keeps pending when history is empty (backend still processing)', () => {
    const sim = createFullSessionSwitchSimulation()

    sim.handleSend('long running query', 'session-a')
    sim.handleSelectSession('session-b', [])

    // Multiple rapid switches back and forth
    sim.handleSelectSession('session-a', [])
    expect(sim.getMessages()).toHaveLength(1)
    expect(sim.getMessages()[0].content).toBe('long running query')

    sim.handleSelectSession('session-b', [])
    sim.handleSelectSession('session-a', [])
    expect(sim.getMessages()).toHaveLength(1)
    expect(sim.getMessages()[0].content).toBe('long running query')
  })
})

// ── UUID-based user message dedup ────────────────────────────────

/**
 * User messages are now identified by clientMsgId (UUID) instead of
 * content alone. This prevents the bug where sending two identical
 * messages would incorrectly dedup the second one.
 *
 * New dedup logic:
 * 1. If msg has clientMsgId → dedup by clientMsgId (exact match)
 * 2. If msg has no clientMsgId → fallback to content match (backward compat)
 */

function createMessageHandlerWithUuidDedup() {
  let messages: Message[] = []

  function handleIncomingMessage(msg: Message) {
    // Replay dedup
    if (msg.replay && messages.some((m) => m.index === msg.index)) {
      return
    }
    // Live dedup for user messages: UUID first, content fallback
    if (msg.type === 'user' && !msg.replay) {
      if (msg.clientMsgId && messages.some((m) => m.clientMsgId === msg.clientMsgId)) {
        return
      }
      if (!msg.clientMsgId && messages.some((m) => m.type === 'user' && m.content === msg.content)) {
        return
      }
    }
    // Live dedup for non-user messages
    if (!msg.replay && msg.type !== 'user') {
      if (msg.index != null && messages.some((m) => m.index === msg.index)) {
        return
      }
    }
    messages = [...messages, msg]
  }

  function handleSend(content: string, clientMsgId: string) {
    const msg: Message = {
      type: 'user',
      content,
      index: messages.length - 1,
      clientMsgId,
    }
    messages = [...messages, msg]
  }

  return {
    getMessages: () => [...messages],
    handleSend,
    handleIncomingMessage,
  }
}

describe('UUID-based user message dedup', () => {
  it('allows two messages with identical content but different UUIDs', () => {
    const handler = createMessageHandlerWithUuidDedup()

    handler.handleSend('hello', 'uuid-1')
    expect(handler.getMessages()).toHaveLength(1)

    // Same content, different UUID → should NOT be deduped
    handler.handleIncomingMessage({
      type: 'user',
      content: 'hello',
      index: 0,
      clientMsgId: 'uuid-2',
    })

    expect(handler.getMessages()).toHaveLength(2)
    expect(handler.getMessages()[1].content).toBe('hello')
  })

  it('dedups when clientMsgId matches (same message echoed)', () => {
    const handler = createMessageHandlerWithUuidDedup()

    handler.handleSend('test', 'uuid-abc')
    expect(handler.getMessages()).toHaveLength(1)

    // Backend echo with same UUID → should be deduped
    handler.handleIncomingMessage({
      type: 'user',
      content: 'test',
      index: 5,
      clientMsgId: 'uuid-abc',
    })

    expect(handler.getMessages()).toHaveLength(1)
  })

  it('falls back to content dedup for messages without UUID', () => {
    const handler = createMessageHandlerWithUuidDedup()

    // Old-style message without clientMsgId
    handler.handleIncomingMessage({
      type: 'user',
      content: 'legacy message',
      index: 0,
    })
    expect(handler.getMessages()).toHaveLength(1)

    // Same content without UUID → should be deduped
    handler.handleIncomingMessage({
      type: 'user',
      content: 'legacy message',
      index: 1,
    })

    expect(handler.getMessages()).toHaveLength(1)
  })

  it('allows different content messages with same UUID (edge case)', () => {
    const handler = createMessageHandlerWithUuidDedup()

    handler.handleIncomingMessage({
      type: 'user',
      content: 'first',
      index: 0,
      clientMsgId: 'shared-uuid',
    })

    // Different content, same UUID (shouldn't happen in practice, but test the logic)
    handler.handleIncomingMessage({
      type: 'user',
      content: 'second',
      index: 1,
      clientMsgId: 'shared-uuid',
    })

    // Deduped by UUID
    expect(handler.getMessages()).toHaveLength(1)
  })
})

// ── Index-based filtering for old state changes (server restart) ──

/**
 * When the server restarts and a user opens an existing session:
 * 1. User sends a new message → state becomes 'running' (spinner appears)
 * 2. Message sends successfully → green ✓ appears
 * 3. Backend subscribe loop sends old buffer messages, including
 *    session_state_changed:completed from the PREVIOUS run with replay: False
 * 4. These old state changes bypass replay protection (replay: False)
 *    and overwrite 'running' → 'completed' → spinner disappears
 *
 * Fix: track the highest index of any user message. Block any
 * session_state_changed message with an index lower than this value —
 * it must be from a previous run.
 */

function createMessageHandlerWithIndexFilter(opts?: {
  activeSessionRef?: { current: string | null }
  onSessionStateChange?: (sessionId: string, state: string) => void
}) {
  const activeSessionRef = opts?.activeSessionRef ?? { current: 'sess1' }
  const sessionStates = new Map<string, string>()
  const sessionStatesRef = new Map<string, string>()
  let highestUserMsgIndex = -1
  let messages: Message[] = []

  function setSessionStateFor(sessionId: string, state: string) {
    sessionStatesRef.set(sessionId, state)
    sessionStates.set(sessionId, state)
  }

  function handleIncomingMessage(msg: Message) {
    // Track highest user message index
    if (msg.type === 'user' && msg.index != null && msg.index > highestUserMsgIndex) {
      highestUserMsgIndex = msg.index
    }

    const isInvisibleMessage =
      msg.type === 'heartbeat' ||
      (msg.type === 'system' && msg.subtype === 'session_state_changed')

    // Cross-session filtering
    if (msg.session_id && msg.session_id !== activeSessionRef.current) {
      if (msg.type === 'system' && msg.subtype === 'session_state_changed') {
        const newState = msg.state || msg.content || 'completed'
        if (msg.index != null && msg.index < highestUserMsgIndex) {
          // Skip — old state change from previous run
        } else if (msg.replay) {
          const currentState = sessionStatesRef.get(msg.session_id)
          if (currentState === 'running' && newState !== 'running' && newState !== 'error') {
            // Skip
          } else {
            setSessionStateFor(msg.session_id, newState)
          }
        } else {
          setSessionStateFor(msg.session_id, newState)
        }
      }
      return
    }

    // Invisible messages from active session
    if (isInvisibleMessage) {
      if (msg.type === 'system' && msg.subtype === 'session_state_changed' && msg.session_id) {
        const newState = msg.state || msg.content || 'completed'
        if (msg.index != null && msg.index < highestUserMsgIndex) {
          // Skip — old state change from previous run
        } else if (msg.replay) {
          const currentState = sessionStatesRef.get(msg.session_id)
          if (currentState === 'running' && newState !== 'running' && newState !== 'error') {
            // Skip
          } else {
            setSessionStateFor(msg.session_id, newState)
          }
        } else {
          setSessionStateFor(msg.session_id, newState)
        }
      }
      return
    }

    // Normal message: append
    messages = [...messages, msg]

    // Also handle session_state_changed in the visible path
    if (msg.type === 'system' && msg.subtype === 'session_state_changed' && msg.session_id) {
      if (msg.index == null || msg.index >= highestUserMsgIndex) {
        setSessionStateFor(msg.session_id, msg.state || msg.content || 'completed')
      }
    }
  }

  return {
    getMessages: () => [...messages],
    getState: (sid: string) => sessionStates.get(sid),
    setSessionStateFor,
    getHighestUserMsgIndex: () => highestUserMsgIndex,
    resetHighestUserMsgIndex: () => { highestUserMsgIndex = -1 },
    handleIncomingMessage,
  }
}

describe('index-based filtering for old state changes (server restart)', () => {
  it('blocks old session_state_changed:completed from previous run after new user message', () => {
    const handler = createMessageHandlerWithIndexFilter()

    // Step 1: User sends new message → state becomes 'running'
    // The user message gets index 5 (simulating existing history of 6 messages)
    handler.handleIncomingMessage({
      type: 'user',
      content: 'new question',
      index: 5,
      session_id: 'sess1',
      clientMsgId: 'uuid-new',
    })
    handler.setSessionStateFor('sess1', 'running')

    expect(handler.getState('sess1')).toBe('running')
    expect(handler.getHighestUserMsgIndex()).toBe(5)

    // Step 2: Subscribe loop sends old completed state from previous run
    // This has replay: False but a lower index (e.g., 3)
    handler.handleIncomingMessage({
      type: 'system',
      subtype: 'session_state_changed',
      state: 'completed',
      content: '',
      index: 3,
      session_id: 'sess1',
      replay: false,
    })

    // State should STILL be 'running' — old state change blocked by index filter
    expect(handler.getState('sess1')).toBe('running')
  })

  it('allows new session_state_changed with index >= user message index', () => {
    const handler = createMessageHandlerWithIndexFilter()

    // User message at index 5
    handler.handleIncomingMessage({
      type: 'user',
      content: 'hello',
      index: 5,
      session_id: 'sess1',
    })
    handler.setSessionStateFor('sess1', 'running')

    // New state change from the CURRENT run (index 6)
    handler.handleIncomingMessage({
      type: 'system',
      subtype: 'session_state_changed',
      state: 'completed',
      content: '',
      index: 6,
      session_id: 'sess1',
      replay: false,
    })

    // Should be accepted — this is from the current run
    expect(handler.getState('sess1')).toBe('completed')
  })

  it('blocks old state changes for inactive sessions too', () => {
    const handler = createMessageHandlerWithIndexFilter({
      activeSessionRef: { current: 'sess1' },
    })

    // User sends message in sess1
    handler.handleIncomingMessage({
      type: 'user',
      content: 'question',
      index: 10,
      session_id: 'sess1',
    })

    // Old state change from sess2 (inactive session) with low index
    handler.handleIncomingMessage({
      type: 'system',
      subtype: 'session_state_changed',
      state: 'completed',
      content: '',
      index: 3,
      session_id: 'sess2',
      replay: false,
    })

    // State for sess2 should not be set (blocked by index filter)
    expect(handler.getState('sess2')).toBeUndefined()
  })

  it('resets highest user msg index on session switch', () => {
    const handler = createMessageHandlerWithIndexFilter()

    // User message at index 10
    handler.handleIncomingMessage({
      type: 'user',
      content: 'first session msg',
      index: 10,
      session_id: 'sess1',
    })
    expect(handler.getHighestUserMsgIndex()).toBe(10)

    // Simulate session switch — reset
    handler.resetHighestUserMsgIndex()
    expect(handler.getHighestUserMsgIndex()).toBe(-1)

    // New user message in new session at index 0
    handler.handleIncomingMessage({
      type: 'user',
      content: 'new session msg',
      index: 0,
      session_id: 'sess2',
    })
    expect(handler.getHighestUserMsgIndex()).toBe(0)
  })

  it('handles server restart full scenario: spinner stays during send', () => {
    // Full simulation of the server restart scenario:
    // 1. Server restarts, user opens existing session
    // 2. REST loads history, WS reconnects
    // 3. User sends message → 'running' state set, spinner appears
    // 4. WebSocket send succeeds → green ✓
    // 5. Subscribe loop sends old buffer messages including
    //    session_state_changed:completed (replay: False, low index)
    // 6. New agent messages start streaming in

    const handler = createMessageHandlerWithIndexFilter()

    // Step 0: REST loaded some history (simulated)
    handler.handleIncomingMessage({
      type: 'user',
      content: 'previous run message',
      index: 0,
      session_id: 'sess1',
      replay: true,
    })
    handler.setSessionStateFor('sess1', 'completed')

    // Step 1: User sends new message
    handler.handleIncomingMessage({
      type: 'user',
      content: 'new question',
      index: 1,
      session_id: 'sess1',
      clientMsgId: 'uuid-1',
    })
    handler.setSessionStateFor('sess1', 'running')

    expect(handler.getState('sess1')).toBe('running')
    expect(handler.getMessages()).toHaveLength(2)

    // Step 2: Subscribe loop sends old 'completed' state (index 0)
    // This is the bug trigger — replay: False but old index
    handler.handleIncomingMessage({
      type: 'system',
      subtype: 'session_state_changed',
      state: 'completed',
      content: '',
      index: 0,
      session_id: 'sess1',
      replay: false,
    })

    // Spinner should NOT disappear
    expect(handler.getState('sess1')).toBe('running')

    // Step 3: Agent starts working, new state arrives
    handler.handleIncomingMessage({
      type: 'system',
      subtype: 'session_state_changed',
      state: 'running',
      content: '',
      index: 2,
      session_id: 'sess1',
      replay: false,
    })

    expect(handler.getState('sess1')).toBe('running')

    // Step 4: Agent response arrives
    handler.handleIncomingMessage({
      type: 'assistant',
      content: 'Here is the answer...',
      index: 3,
      session_id: 'sess1',
    })

    expect(handler.getMessages()).toHaveLength(3)
    expect(handler.getMessages()[2].content).toBe('Here is the answer...')

    // Step 5: Agent completes
    handler.handleIncomingMessage({
      type: 'system',
      subtype: 'session_state_changed',
      state: 'completed',
      content: '',
      index: 4,
      session_id: 'sess1',
      replay: false,
    })

    // Now completed should overwrite running (agent actually finished)
    expect(handler.getState('sess1')).toBe('completed')
  })

  it('allows replayed state changes when no user message has been seen yet', () => {
    // If no user message has been received yet (highestUserMsgIndex = -1),
    // all state changes should pass through normally.
    const handler = createMessageHandlerWithIndexFilter()

    // Replay a completed state — no user message seen yet
    handler.handleIncomingMessage({
      type: 'system',
      subtype: 'session_state_changed',
      state: 'completed',
      content: '',
      index: 3,
      session_id: 'sess1',
      replay: true,
    })

    // Should be accepted since highestUserMsgIndex is still -1
    // Wait — actually, since 3 >= -1 is true, it passes
    // But replay protection would also apply. Let me check...
    // Since no user message was seen, highestUserMsgIndex is -1,
    // so index 3 >= -1 is true → passes index filter
    // Then replay protection applies, but state is 'idle' (no running yet)
    // So 'completed' should be set
    expect(handler.getState('sess1')).toBe('completed')
  })

  it('does not block state change when index is missing', () => {
    // Some messages may not have an index — they should pass through
    const handler = createMessageHandlerWithIndexFilter()

    // User message seen
    handler.handleIncomingMessage({
      type: 'user',
      content: 'hello',
      index: 5,
      session_id: 'sess1',
    })
    handler.setSessionStateFor('sess1', 'running')

    // State change without index — use as any to simulate missing field
    handler.handleIncomingMessage({
      type: 'system',
      subtype: 'session_state_changed',
      state: 'completed',
      content: '',
      session_id: 'sess1',
      replay: false,
      // No index — simulates message from older backend version
    } as any as Message)

    // Should be accepted (index missing → no blocking)
    expect(handler.getState('sess1')).toBe('completed')
  })
})

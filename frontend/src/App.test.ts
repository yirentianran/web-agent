import { describe, it, expect, beforeEach } from 'vitest'
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

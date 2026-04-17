import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import ChatArea from '../components/ChatArea'
import type { Message } from '../lib/types'

interface RenderResult {
  messagesContainer: HTMLElement
  scrollPositions: Map<string, number>
  rerender: ReturnType<typeof render>['rerender']
  container: ReturnType<typeof render>['container']
}

/**
 * JSDom doesn't fully support scrollHeight/clientHeight. We mock them
 * so scroll behavior tests work correctly.
 */
function mockScrollContainer(container: HTMLElement, opts?: { scrollHeight?: number; clientHeight?: number }) {
  const { scrollHeight = 1000, clientHeight = 500 } = opts || {}
  Object.defineProperty(container, 'scrollHeight', { value: scrollHeight, writable: true, configurable: true })
  Object.defineProperty(container, 'clientHeight', { value: clientHeight, writable: true, configurable: true })
  Object.defineProperty(container, 'scrollTop', { value: 0, writable: true, configurable: true })
}

function renderChatArea(messages: Message[], opts?: { sessionId?: string | null; sessionState?: string }): RenderResult {
  const { sessionId = 'test-session', sessionState = 'idle' } = opts || {}
  const scrollPositions = new Map<string, number>()
  const result = render(
    <ChatArea
      messages={messages}
      sessionId={sessionId}
      sessionState={sessionState}
      onAnswer={() => {}}
      scrollPositions={scrollPositions}
    />,
  )
  const messagesContainer = result.container.querySelector('.messages') as HTMLElement
  return { messagesContainer, scrollPositions, rerender: result.rerender, container: result.container }
}

/**
 * Returns the index of an element among message siblings.
 * Used to verify DOM ordering.
 */
function getDomOrder(element: HTMLElement): number {
  const messageEl = element.closest('.message') || element.parentElement
  const parent = messageEl?.parentElement
  if (!parent) return 0
  const messageSiblings = Array.from(parent.querySelectorAll('.message'))
  return messageSiblings.indexOf(messageEl!)
}

describe('ChatArea - message ordering', () => {
  it('renders messages in chronological order (ascending by index)', () => {
    const messages: Message[] = [
      { type: 'user', content: 'First message', index: 2 },
      { type: 'assistant', content: 'Second message', index: 3 },
      { type: 'user', content: 'Third message', index: 5 },
    ]

    renderChatArea(messages)

    const first = screen.getByText('First message')
    const second = screen.getByText('Second message')
    const third = screen.getByText('Third message')

    expect(getDomOrder(first)).toBe(0)
    expect(getDomOrder(second)).toBe(1)
    expect(getDomOrder(third)).toBe(2)
  })

  it('renders out-of-order messages sorted by index', () => {
    // Messages intentionally provided out of order
    const messages: Message[] = [
      { type: 'user', content: 'Last', index: 10 },
      { type: 'assistant', content: 'Middle', index: 5 },
      { type: 'user', content: 'First', index: 1 },
    ]

    renderChatArea(messages)

    const first = screen.getByText('First')
    const middle = screen.getByText('Middle')
    const last = screen.getByText('Last')

    expect(getDomOrder(first)).toBe(0)
    expect(getDomOrder(middle)).toBe(1)
    expect(getDomOrder(last)).toBe(2)
  })

  it('handles empty messages array', () => {
    renderChatArea([])

    // Welcome screen should be shown
    expect(screen.getByText('Web Agent')).toBeInTheDocument()
  })
})

describe('ChatArea - auto-scroll to bottom', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  it('scrolls to bottom on first visit to a session', () => {
    const messages: Message[] = [
      { type: 'user', content: 'Hello', index: 0 },
      { type: 'assistant', content: 'Hi there!', index: 1 },
    ]

    const { messagesContainer, scrollPositions } = renderChatArea(messages)
    mockScrollContainer(messagesContainer, { scrollHeight: 1000, clientHeight: 500 })

    // First visit should have scrolled to bottom
    act(() => { vi.advanceTimersByTime(50) })

    // The container's scrollTop should have been set to scrollHeight (1000)
    expect(messagesContainer.scrollTop).toBe(1000)
    // No scroll position should be saved for first visit
    expect(scrollPositions.has('test-session')).toBe(false)
  })

  it('auto-scrolls when new messages arrive and user is at bottom', () => {
    const initialMessages: Message[] = [
      { type: 'user', content: 'Hello', index: 0 },
    ]

    const { messagesContainer, rerender } = renderChatArea(initialMessages, { sessionState: 'completed' })
    mockScrollContainer(messagesContainer, { scrollHeight: 500, clientHeight: 500 })
    // User is at bottom (scrollHeight === clientHeight, distance = 0)

    // Add a new message
    const newMessages: Message[] = [
      { type: 'user', content: 'Hello', index: 0 },
      { type: 'assistant', content: 'Hi there!', index: 1 },
    ]

    const scrollPositions = new Map<string, number>()
    act(() => {
      rerender(
        <ChatArea
          messages={newMessages}
          sessionId="test-session"
          sessionState="completed"
          onAnswer={() => {}}
          scrollPositions={scrollPositions}
        />,
      )
    })
    act(() => { vi.advanceTimersByTime(50) })

    // Should have auto-scrolled to bottom
    expect(messagesContainer.scrollTop).toBe(500)
  })

  it('does NOT auto-scroll when user has scrolled up', () => {
    const initialMessages: Message[] = [
      { type: 'user', content: 'Hello', index: 0 },
      { type: 'assistant', content: 'Hi!', index: 1 },
    ]

    const { messagesContainer, rerender } = renderChatArea(initialMessages, { sessionState: 'completed' })
    mockScrollContainer(messagesContainer, { scrollHeight: 1000, clientHeight: 500 })

    // Let the initial render's scrollToBottom RAF fire first
    act(() => { vi.advanceTimersByTime(50) })

    // Now simulate user scrolling up (not at bottom)
    Object.defineProperty(messagesContainer, 'scrollTop', { value: 200, writable: true, configurable: true })
    fireEvent.scroll(messagesContainer)

    // Add a new message
    const newMessages: Message[] = [
      ...initialMessages,
      { type: 'assistant', content: 'Follow up!', index: 2 },
    ]

    const scrollPositions = new Map<string, number>()
    act(() => {
      rerender(
        <ChatArea
          messages={newMessages}
          sessionId="test-session"
          sessionState="completed"
          onAnswer={() => {}}
          scrollPositions={scrollPositions}
        />,
      )
    })
    act(() => { vi.advanceTimersByTime(50) })

    // Should NOT have auto-scrolled (user is reading history)
    expect(messagesContainer.scrollTop).toBe(200)
  })

  it('resumes auto-scroll after user scrolls back to bottom', () => {
    const initialMessages: Message[] = [
      { type: 'user', content: 'Hello', index: 0 },
      { type: 'assistant', content: 'Hi!', index: 1 },
    ]

    const { messagesContainer, rerender } = renderChatArea(initialMessages, { sessionState: 'completed' })
    mockScrollContainer(messagesContainer, { scrollHeight: 1000, clientHeight: 500 })

    // Let initial RAF fire
    act(() => { vi.advanceTimersByTime(50) })

    // User scrolled up
    Object.defineProperty(messagesContainer, 'scrollTop', { value: 200, writable: true, configurable: true })
    fireEvent.scroll(messagesContainer)

    // User scrolls back to bottom (within 100px threshold)
    Object.defineProperty(messagesContainer, 'scrollTop', { value: 450, writable: true, configurable: true })
    // 1000 - 450 - 500 = 50 <= 100, so considered "at bottom"
    fireEvent.scroll(messagesContainer)

    // Add a new message
    const newMessages: Message[] = [
      ...initialMessages,
      { type: 'assistant', content: 'Follow up!', index: 2 },
    ]

    const scrollPositions = new Map<string, number>()
    act(() => {
      rerender(
        <ChatArea
          messages={newMessages}
          sessionId="test-session"
          sessionState="completed"
          onAnswer={() => {}}
          scrollPositions={scrollPositions}
        />,
      )
    })
    act(() => { vi.advanceTimersByTime(50) })

    // Should auto-scroll to bottom again
    expect(messagesContainer.scrollTop).toBe(1000)
  })

  it('resets "at bottom" state when switching sessions', () => {
    const messages: Message[] = [
      { type: 'user', content: 'Hello', index: 0 },
    ]

    const { messagesContainer, rerender } = renderChatArea(messages, { sessionId: 'session-a' })
    mockScrollContainer(messagesContainer, { scrollHeight: 1000, clientHeight: 500 })
    act(() => { vi.advanceTimersByTime(50) })

    // Switch to a different session
    const scrollPositions = new Map<string, number>()
    act(() => {
      rerender(
        <ChatArea
          messages={messages}
          sessionId="session-b"
          sessionState="idle"
          onAnswer={() => {}}
          scrollPositions={scrollPositions}
        />,
      )
    })
    act(() => { vi.advanceTimersByTime(50) })

    // New session should have scrolled to bottom (first visit)
    expect(messagesContainer.scrollTop).toBe(1000)
  })
})

describe('ChatArea - auto-scroll on session running', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  it('auto-scrolls to bottom when session starts running, even if user is in the middle', () => {
    const messages: Message[] = [
      { type: 'user', content: 'Hello', index: 0 },
      { type: 'assistant', content: 'Hi!', index: 1 },
    ]

    const { messagesContainer, rerender } = renderChatArea(messages, { sessionState: 'idle' })
    mockScrollContainer(messagesContainer, { scrollHeight: 1000, clientHeight: 500 })
    act(() => { vi.advanceTimersByTime(50) })

    // User scrolled to the middle
    Object.defineProperty(messagesContainer, 'scrollTop', { value: 300, writable: true, configurable: true })
    fireEvent.scroll(messagesContainer)
    expect(messagesContainer.scrollTop).toBe(300)

    // Session starts running (agent begins responding)
    const scrollPositions = new Map<string, number>()
    act(() => {
      rerender(
        <ChatArea
          messages={messages}
          sessionId="test-session"
          sessionState="running"
          onAnswer={() => {}}
          scrollPositions={scrollPositions}
        />,
      )
    })
    act(() => { vi.advanceTimersByTime(50) })

    // Should have scrolled to bottom triggered by running state
    expect(messagesContainer.scrollTop).toBe(1000)
  })

  it('stops auto-scrolling after running trigger if user scrolls away again', () => {
    const messages: Message[] = [
      { type: 'user', content: 'Hello', index: 0 },
    ]

    const { messagesContainer, rerender } = renderChatArea(messages, { sessionState: 'running' })
    mockScrollContainer(messagesContainer, { scrollHeight: 1000, clientHeight: 500 })
    act(() => { vi.advanceTimersByTime(50) })

    // Initial running should have scrolled to bottom
    expect(messagesContainer.scrollTop).toBe(1000)

    // User scrolls up while agent is still running (clearly not at bottom)
    Object.defineProperty(messagesContainer, 'scrollTop', { value: 100, writable: true, configurable: true })
    fireEvent.scroll(messagesContainer)

    // New messages arrive (simulating agent output)
    const newMessages: Message[] = [
      ...messages,
      { type: 'assistant', content: 'Working on it...', index: 1 },
    ]

    const scrollPositions = new Map<string, number>()
    act(() => {
      rerender(
        <ChatArea
          messages={newMessages}
          sessionId="test-session"
          sessionState="running"
          onAnswer={() => {}}
          scrollPositions={scrollPositions}
        />,
      )
    })
    act(() => { vi.advanceTimersByTime(50) })

    // Should NOT auto-scroll because user scrolled away after the running trigger
    expect(messagesContainer.scrollTop).toBe(100)
  })

  it('re-triggers auto-scroll when session transitions to running again', () => {
    const messages: Message[] = [
      { type: 'user', content: 'Hello', index: 0 },
    ]

    const { messagesContainer, rerender } = renderChatArea(messages, { sessionState: 'idle' })
    mockScrollContainer(messagesContainer, { scrollHeight: 1000, clientHeight: 500 })
    act(() => { vi.advanceTimersByTime(50) })

    // User scrolls to middle
    Object.defineProperty(messagesContainer, 'scrollTop', { value: 300, writable: true, configurable: true })
    fireEvent.scroll(messagesContainer)

    // Session goes to running
    const scrollPositions = new Map<string, number>()
    act(() => {
      rerender(
        <ChatArea
          messages={messages}
          sessionId="test-session"
          sessionState="running"
          onAnswer={() => {}}
          scrollPositions={scrollPositions}
        />,
      )
    })
    act(() => { vi.advanceTimersByTime(50) })
    expect(messagesContainer.scrollTop).toBe(1000)

    // User scrolls up again
    Object.defineProperty(messagesContainer, 'scrollTop', { value: 100, writable: true, configurable: true })
    fireEvent.scroll(messagesContainer)

    // Session completes, then starts running again (follow-up message)
    act(() => {
      rerender(
        <ChatArea
          messages={messages}
          sessionId="test-session"
          sessionState="completed"
          onAnswer={() => {}}
          scrollPositions={scrollPositions}
        />,
      )
    })
    act(() => { vi.advanceTimersByTime(50) })

    // User scrolls up again while completed
    Object.defineProperty(messagesContainer, 'scrollTop', { value: 100, writable: true, configurable: true })
    fireEvent.scroll(messagesContainer)

    // Session starts running again
    act(() => {
      rerender(
        <ChatArea
          messages={messages}
          sessionId="test-session"
          sessionState="running"
          onAnswer={() => {}}
          scrollPositions={scrollPositions}
        />,
      )
    })
    act(() => { vi.advanceTimersByTime(50) })

    // Should have re-triggered scroll to bottom
    expect(messagesContainer.scrollTop).toBe(1000)
  })

  it('does not auto-scroll when session is idle and user is in the middle', () => {
    const messages: Message[] = [
      { type: 'user', content: 'Hello', index: 0 },
      { type: 'assistant', content: 'Hi!', index: 1 },
    ]

    const { messagesContainer, rerender } = renderChatArea(messages, { sessionState: 'idle' })
    mockScrollContainer(messagesContainer, { scrollHeight: 1000, clientHeight: 500 })
    act(() => { vi.advanceTimersByTime(50) })

    // User scrolls to middle (clearly not at bottom: distance = 500px)
    Object.defineProperty(messagesContainer, 'scrollTop', { value: 100, writable: true, configurable: true })
    fireEvent.scroll(messagesContainer)

    // More messages arrive but session stays idle
    const newMessages: Message[] = [
      ...messages,
      { type: 'assistant', content: 'Extra!', index: 2 },
    ]

    const scrollPositions = new Map<string, number>()
    act(() => {
      rerender(
        <ChatArea
          messages={newMessages}
          sessionId="test-session"
          sessionState="idle"
          onAnswer={() => {}}
          scrollPositions={scrollPositions}
        />,
      )
    })
    act(() => { vi.advanceTimersByTime(50) })

    // Should NOT auto-scroll (user is in the middle, not running)
    expect(messagesContainer.scrollTop).toBe(100)
  })
})

// ── Hook spinners removed — no spinner should appear ──────────────────

describe('Hook spinners removed', () => {
  it('does NOT show hook spinner when hook_started message arrives', () => {
    const messages: Message[] = [
      { type: 'system', subtype: 'hook_started', hook_id: 'hk-1', hook_name: 'startup', content: '', index: 0 },
    ]

    const { container } = renderChatArea(messages, { sessionState: 'running' })
    // Hook spinner variant should not exist
    expect(container.querySelector('.status-spinner--hook')).not.toBeInTheDocument()
    // Agent spinner should show instead
    expect(container.querySelector('.status-spinner--agent')).toBeInTheDocument()
  })

  it('does NOT show hook spinner even with multiple hook_started messages', () => {
    const messages: Message[] = [
      { type: 'system', subtype: 'hook_started', hook_id: 'hk-1', hook_name: 'startup', content: '', index: 0 },
      { type: 'system', subtype: 'hook_started', hook_id: 'hk-2', hook_name: 'shutdown', content: '', index: 1 },
    ]

    const { container } = renderChatArea(messages, { sessionState: 'running' })
    // Only agent spinner, no hook spinners
    expect(container.querySelectorAll('.status-spinner--hook').length).toBe(0)
    expect(container.querySelector('.status-spinner--agent')).toBeInTheDocument()
  })

  it('still shows agent spinner when sessionState is running', () => {
    const messages: Message[] = [
      { type: 'user', content: 'Hello', index: 0 },
    ]

    const { container } = renderChatArea(messages, { sessionState: 'running' })
    expect(container.querySelector('.status-spinner')).toBeInTheDocument()
    expect(screen.getByText('Agent is working...')).toBeInTheDocument()
  })

  it('does NOT show any spinner when sessionState is completed', () => {
    const messages: Message[] = [
      { type: 'user', content: 'Hello', index: 0 },
      { type: 'system', subtype: 'hook_started', hook_id: 'hk-1', hook_name: 'startup', content: '', index: 1 },
    ]

    const { container } = renderChatArea(messages, { sessionState: 'completed' })
    expect(container.querySelector('.status-spinner')).not.toBeInTheDocument()
  })
})

// ── Hook tracking in terminal states ──────────────────────────────

describe('Hook tracking', () => {
  it('clears running hooks when sessionState is completed', () => {
    const messages: Message[] = [
      { type: 'system', subtype: 'hook_started', hook_id: 'hk-1', hook_name: 'startup', content: '', index: 0 },
      { type: 'system', subtype: 'session_state_changed', state: 'completed', content: '', index: 1 },
    ]

    const { container } = renderChatArea(messages, { sessionState: 'completed' })
    expect(container.querySelector('.status-spinner')).not.toBeInTheDocument()
  })

  it('shows error message text when sessionState is error', () => {
    const messages: Message[] = [
      { type: 'user', content: 'Hello', index: 0 },
    ]

    const { container } = renderChatArea(messages, { sessionState: 'error' })
    // Spinner should be gone
    expect(container.querySelector('.status-spinner')).not.toBeInTheDocument()
    // Generic error message should be visible
    expect(screen.getByText('Session ended with an error. Try sending a new message.')).toBeInTheDocument()
  })

  it('clears running hooks when sessionState is cancelled', () => {
    const messages: Message[] = [
      { type: 'system', subtype: 'hook_started', hook_id: 'hk-1', hook_name: 'startup', content: '', index: 0 },
    ]

    const { container } = renderChatArea(messages, { sessionState: 'cancelled' })
    expect(container.querySelector('.status-spinner')).not.toBeInTheDocument()
  })

  it('shows agent spinner, not hook spinner, when hooks are cleared by terminal state', () => {
    const messages: Message[] = [
      { type: 'system', subtype: 'hook_started', hook_id: 'hk-1', hook_name: 'startup', content: '', index: 0 },
      { type: 'system', subtype: 'session_state_changed', state: 'completed', content: '', index: 1 },
    ]

    const { container } = renderChatArea(messages, { sessionState: 'completed' })
    expect(container.querySelector('.status-spinner')).not.toBeInTheDocument()
  })
})

import { useEffect, useRef, useCallback, useState, useMemo } from 'react'
import MessageBubble from './MessageBubble'
import SkillFeedbackWidget from './SkillFeedbackWidget'
import StatusSpinner from './StatusSpinner'
import type { Message } from '../lib/types'

const SCROLL_THRESHOLD = 100 // pixels from bottom to consider "at bottom"

interface RunningHook {
  hook_id: string
  hook_name: string
  hook_event: string
}

interface ChatAreaProps {
  messages: Message[]
  sessionId: string | null
  sessionState: string
  onAnswer: (sessionId: string, answers: Record<string, string>) => void
  scrollPositions: Map<string, number>
  onFileClick?: (filename: string) => void
}

export default function ChatArea({ messages, sessionId, sessionState, onAnswer, scrollPositions, onFileClick }: ChatAreaProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const visitedRef = useRef<Set<string>>(new Set())
  const scrollRestoredRef = useRef(false)
  const isUserAtBottomRef = useRef(true)
  const [runningHooks, setRunningHooks] = useState<Map<string, RunningHook>>(new Map())
  const [agentStartTime, setAgentStartTime] = useState<number | null>(null)

  const handleScroll = useCallback(() => {
    const container = containerRef.current
    if (!container) return

    // Detect whether user is near the bottom
    const { scrollTop, scrollHeight, clientHeight } = container
    const distanceFromBottom = scrollHeight - scrollTop - clientHeight
    isUserAtBottomRef.current = distanceFromBottom <= SCROLL_THRESHOLD

    // Always save scroll position for session restore
    if (sessionId) {
      scrollPositions.set(sessionId, scrollTop)
    }
  }, [sessionId, scrollPositions])

  const scrollToBottom = useCallback(() => {
    const container = containerRef.current
    if (!container) return
    requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight
    })
  }, [])

  // Track running hooks based on system messages
  useEffect(() => {
    const recentMessages = messages.slice(-10)

    for (const msg of recentMessages) {
      if (msg.type === 'system') {
        if (msg.subtype === 'hook_started' && msg.hook_id && msg.hook_name) {
          setRunningHooks(prev => {
            const next = new Map(prev)
            if (!next.has(msg.hook_id!)) {
              next.set(msg.hook_id!, {
                hook_id: msg.hook_id!,
                hook_name: msg.hook_name!,
                hook_event: msg.hook_event || ''
              })
            }
            return next
          })
        } else if (msg.subtype === 'hook_response' && msg.hook_id) {
          setRunningHooks(prev => {
            const next = new Map(prev)
            next.delete(msg.hook_id!)
            return next
          })
        }
      }
    }
  }, [messages])

  // Clear running hooks when session enters a terminal state.
  // Prevents the "Running hook: startup" spinner from persisting
  // forever when hook_response is lost or never sent.
  useEffect(() => {
    if (sessionState === 'completed' || sessionState === 'error' || sessionState === 'cancelled') {
      setRunningHooks(new Map())
    }
  }, [sessionState])

  // Track when agent started running.
  // Uses a ref to detect transitions into 'running' so follow-ups
  // correctly reset the elapsed timer. Heartbeats update a stale
  // counter but do NOT reset the elapsed timer (that caused the
  // "timer jumps back to 0" bug).
  const prevSessionStateRef = useRef<string | null>(null)
  const heartbeatCountRef = useRef(0)

  useEffect(() => {
    // Detect transition TO running — always reset the start time
    if (sessionState === 'running' && prevSessionStateRef.current !== 'running') {
      setAgentStartTime(Date.now())
    }
    // Transition AWAY from running — clear the start time
    if (sessionState !== 'running') {
      setAgentStartTime(null)
    }
    // Count heartbeats for stale detection (don't affect elapsed timer)
    heartbeatCountRef.current = messages.filter(m => m.type === 'heartbeat').length

    prevSessionStateRef.current = sessionState
  }, [sessionState, messages])

  // Restore scroll position when session changes
  useEffect(() => {
    if (!sessionId || !containerRef.current) return

    const isFirstVisit = !visitedRef.current.has(sessionId)
    if (isFirstVisit) {
      visitedRef.current.add(sessionId)
      scrollRestoredRef.current = false
      // First visit: scroll to bottom to show latest messages
      scrollToBottom()
      return
    }

    const savedPos = scrollPositions.get(sessionId)
    if (savedPos !== undefined && !scrollRestoredRef.current) {
      scrollRestoredRef.current = true
      requestAnimationFrame(() => {
        if (containerRef.current && containerRef.current.scrollTop !== savedPos) {
          containerRef.current.scrollTop = savedPos
        }
      })
    }
  }, [sessionId, messages, scrollPositions, scrollToBottom])

  // Reset "at bottom" state when session changes (not on every render)
  const prevSessionIdRef = useRef<string | null>(null)
  useEffect(() => {
    if (prevSessionIdRef.current !== sessionId) {
      isUserAtBottomRef.current = true
      prevSessionIdRef.current = sessionId
    }
  }, [sessionId])

  // Auto-scroll to bottom when session transitions to "running"
  // This triggers even if user is in the middle, giving them a chance to see new activity
  const prevStateRef = useRef<string | null>(null)
  useEffect(() => {
    if (sessionState === 'running' && prevStateRef.current !== 'running') {
      // User started interacting or agent started responding — scroll to bottom
      isUserAtBottomRef.current = true
      scrollToBottom()
    }
    prevStateRef.current = sessionState
  }, [sessionState, scrollToBottom])

  // Auto-scroll to bottom when new messages arrive, only if user is at bottom
  useEffect(() => {
    if (isUserAtBottomRef.current) {
      scrollToBottom()
    }
  }, [messages, scrollToBottom])

  // Determine what spinner to show
  const hasRunningHooks = runningHooks.size > 0
  const isAgentRunning = sessionState === 'running' && !hasRunningHooks

  // Simplify hook name for display
  const getHookDisplayName = (hookName: string) => {
    return hookName.includes(':') ? hookName.split(':')[1] : hookName
  }

  // Sort messages by index to ensure chronological order (newest at bottom)
  const sortedMessages = useMemo(
    () => [...messages].sort((a, b) => a.index - b.index),
    [messages],
  )

  // Keep only the latest TodoWrite message — hide all earlier updates.
  // TodoWrite is a stateful progress widget; showing every snapshot
  // creates stacked duplicate progress bars.
  const filteredMessages = useMemo(() => {
    let lastTodoWriteIndex = -1
    for (let i = sortedMessages.length - 1; i >= 0; i--) {
      if (
        sortedMessages[i].type === 'tool_use' &&
        sortedMessages[i].name === 'TodoWrite'
      ) {
        lastTodoWriteIndex = sortedMessages[i].index
        break
      }
    }
    return sortedMessages.filter(
      (msg) =>
        msg.type !== 'tool_use' ||
        msg.name !== 'TodoWrite' ||
        msg.index === lastTodoWriteIndex,
    )
  }, [sortedMessages])

  // Filter out invisible message types for the welcome screen check.
  // If a session only has heartbeats / internal state messages, show the welcome screen.
  const hasVisibleMessages = useMemo(() => {
    return messages.some((msg) => {
      if (msg.type === 'heartbeat') return false
      if (msg.type === 'system' && msg.subtype && [
        'hook_started', 'hook_response', 'hook_error',
        'init', 'session_state_changed'
      ].includes(msg.subtype)) return false
      if (msg.type === 'user' && (!msg.content || !msg.content.trim())) {
        const files = (msg.data as Array<{ filename: string }> | undefined) || []
        if (files.length === 0) return false
      }
      return true
    })
  }, [messages])

  return (
    <div className="chat-area">
      <div className="messages" ref={containerRef} onScroll={handleScroll}>
        {!hasVisibleMessages && (
          <div className="chat-welcome">
            <div className="welcome-logo">◎</div>
            <h1 className="welcome-title">Web Agent</h1>
            <p className="welcome-desc">Your AI-powered companion</p>
          </div>
        )}

        {filteredMessages.map((msg, i) => (
          <MessageBubble
            key={`${msg.index}-${i}`}
            message={msg}
            sessionId={sessionId || ''}
            onAnswer={onAnswer}
            onFileClick={onFileClick}
          />
        ))}

        {/* Show hook spinners */}
        {hasRunningHooks && Array.from(runningHooks.values()).map(hook => (
          <div key={hook.hook_id} className="message system-message">
            <StatusSpinner
              variant="hook"
              text="Running hook:"
              detail={getHookDisplayName(hook.hook_name)}
            />
          </div>
        ))}

        {/* Show agent spinner when no hooks are running */}
        {isAgentRunning && (
          <div className="message system-message">
            <StatusSpinner
              variant="agent"
              text="Agent is working..."
              startTime={agentStartTime ?? undefined}
            />
          </div>
        )}

        {sessionState === 'error' && (
          <div className="message system-message">
            <span className="system-text error-text">Session ended with an error. Try sending a new message.</span>
          </div>
        )}
      </div>

      {sessionState === 'completed' && (
        <SkillFeedbackWidget
          onSubmit={async (rating, comment) => {
            await fetch('/api/skills/general/feedback', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ rating, comment, session_id: sessionId }),
            })
          }}
        />
      )}
    </div>
  )
}
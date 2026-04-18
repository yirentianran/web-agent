import { useEffect, useRef, useCallback, useState, useMemo } from 'react'
import MessageBubble from './MessageBubble'
import SkillFeedbackWidget from './SkillFeedbackWidget'
import StatusSpinner from './StatusSpinner'
import type { Message } from '../lib/types'

const SCROLL_THRESHOLD = 100 // pixels from bottom to consider "at bottom"

interface ChatAreaProps {
  messages: Message[]
  sessionId: string | null
  sessionState: string
  onAnswer: (sessionId: string, answers: Record<string, string>) => void
  scrollPositions: Map<string, number>
  onFileClick?: (filename: string) => void
  authToken?: string | null
}

export default function ChatArea({ messages, sessionId, sessionState, onAnswer, scrollPositions, onFileClick, authToken }: ChatAreaProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const visitedRef = useRef<Set<string>>(new Set())
  const scrollRestoredRef = useRef(false)
  const isUserAtBottomRef = useRef(true)
  const [agentStartTime, setAgentStartTime] = useState<number | null>(null)

  const handleScroll = useCallback(() => {
    const container = containerRef.current
    if (!container) return

    // Detect whether user is near the bottom
    const { scrollTop, scrollHeight, clientHeight } = container
    const distanceFromBottom = scrollHeight - scrollTop - clientHeight
    isUserAtBottomRef.current = distanceFromBottom <= SCROLL_THRESHOLD

    // Save scroll position to localStorage for session restore
    if (sessionId) {
      scrollPositions.set(sessionId, scrollTop)
      // Also persist to localStorage so it survives page refresh
      try {
        const SCROLL_STORAGE_KEY = 'web-agent-scroll-positions'
        const positions = new Map<string, number>()
        // Read current positions from localStorage
        const raw = localStorage.getItem(SCROLL_STORAGE_KEY)
        if (raw) {
          const parsed = JSON.parse(raw) as [string, number][]
          parsed.forEach(([k, v]) => positions.set(k, v))
        }
        positions.set(sessionId, scrollTop)
        localStorage.setItem(SCROLL_STORAGE_KEY, JSON.stringify(Array.from(positions)))
      } catch {
        // localStorage full or unavailable — skip
      }
    }
  }, [sessionId, scrollPositions])

  const scrollToBottom = useCallback(() => {
    const container = containerRef.current
    if (!container) return
    requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight
    })
  }, [])

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

  // Restore scroll position when session changes or messages load
  useEffect(() => {
    if (!sessionId || !containerRef.current) return

    const isFirstVisit = !visitedRef.current.has(sessionId)
    if (isFirstVisit) {
      visitedRef.current.add(sessionId)

      // Running sessions: always scroll to bottom to show latest activity
      if (sessionState === 'running') {
        scrollRestoredRef.current = false
        scrollToBottom()
        return
      }

      // Try to restore from localStorage (survives page refresh)
      try {
        const SCROLL_STORAGE_KEY = 'web-agent-scroll-positions'
        const raw = localStorage.getItem(SCROLL_STORAGE_KEY)
        if (raw) {
          const parsed = JSON.parse(raw) as [string, number][]
          const savedPos = parsed.find(([k]) => k === sessionId)?.[1]
          if (savedPos !== undefined) {
            scrollRestoredRef.current = true
            requestAnimationFrame(() => {
              if (containerRef.current) {
                containerRef.current.scrollTop = savedPos
              }
            })
            return
          }
        }
      } catch {
        // localStorage unavailable — fall through to scroll-to-bottom
      }

      // No saved position: first real visit, scroll to bottom
      scrollRestoredRef.current = false
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
  const isAgentRunning = sessionState === 'running'

  // Find the index of the latest TodoWrite message so MessageBubble can
  // hide older TodoWrite visualizations (deduplicate todo lists).
  const lastTodoWriteIndex = useMemo(() => {
    let maxIndex = -1
    for (const msg of messages) {
      if (msg.type === 'tool_use' && msg.name === 'TodoWrite' && msg.index > maxIndex) {
        maxIndex = msg.index
      }
    }
    return maxIndex === -1 ? undefined : maxIndex
  }, [messages])

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
        'init', 'session_state_changed', 'session_cancelled'
      ].includes(msg.subtype)) return false
      if (msg.type === 'user' && (!msg.content || !msg.content.trim())) {
        const files = (msg.data as Array<{ filename: string }> | undefined) || []
        if (files.length === 0) return false
      }
      return true
    })
  }, [messages])

  // Derive skill name from tool_use messages for feedback endpoint.
  // If exactly one skill was used, use its name; otherwise fallback to "general".
  const feedbackSkillName = useMemo(() => {
    const skillTools = new Set<string>()
    for (const msg of messages) {
      if (msg.type === 'tool_use' && msg.name) {
        skillTools.add(msg.name)
      }
    }
    return skillTools.size === 1 ? skillTools.values().next().value : 'general'
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
            lastTodoWriteIndex={lastTodoWriteIndex}
          />
        ))}

        {/* Show agent spinner when session is running */}
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
          skillName={feedbackSkillName}
          onSubmit={async (rating, comment, userEdits) => {
            const headers: Record<string, string> = { 'Content-Type': 'application/json' }
            if (authToken) headers['Authorization'] = `Bearer ${authToken}`
            await fetch(`/api/skills/${feedbackSkillName}/feedback`, {
              method: 'POST',
              headers,
              body: JSON.stringify({ rating, comment, user_edits: userEdits, session_id: sessionId }),
            })
          }}
        />
      )}
    </div>
  )
}
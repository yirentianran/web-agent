import { useState, useCallback, useEffect, useRef, type FormEvent } from 'react'
import Sidebar from './components/Sidebar'
import Header from './components/Header'
import ChatArea from './components/ChatArea'
import InputBar, { type InputBarHandle } from './components/InputBar'
import SettingsPanel from './components/SettingsPanel'
import FilesPanel from './components/FilesPanel'
import DesignPreviewPage from './DesignPreviewPage'
import SettingsPreviewPage from './SettingsPreviewPage'
import TechPreviewPage from './TechPreviewPage'
import { useWebSocket } from './hooks/useWebSocket'
import type { Message, SessionItem } from './lib/types'

const logger = {
  error: (message: string, err: unknown) => {
    const detail = err instanceof Error ? err.message : String(err)
    // In production, replace with a real logger (e.g., pino)
    // eslint-disable-next-line no-console
    console.error(`[App] ${message}: ${detail}`)
  },
}

// Persist scroll position to localStorage so it survives page refresh
const SCROLL_STORAGE_KEY = 'web-agent-scroll-positions'

function loadScrollPositions(): Map<string, number> {
  try {
    const raw = localStorage.getItem(SCROLL_STORAGE_KEY)
    if (!raw) return new Map()
    const parsed = JSON.parse(raw) as [string, number][]
    return new Map(parsed)
  } catch {
    return new Map()
  }
}

const sessionScrollPositions = loadScrollPositions()

// Check if we're on design preview page
function isDesignPreviewRoute(): boolean {
  return window.location.pathname === '/design-preview' || window.location.hash === '#/design-preview'
}

// Check if we're on settings preview page
function isSettingsPreviewRoute(): boolean {
  return window.location.pathname === '/settings-preview' || window.location.hash === '#/settings-preview'
}

// Check if we're on tech preview page
function isTechPreviewRoute(): boolean {
  return window.location.pathname === '/tech-preview' || window.location.hash === '#/tech-preview'
}

interface LoginScreenProps {
  onLogin: (userId: string) => void
}

function LoginScreen({ onLogin }: LoginScreenProps) {
  const [userId, setUserId] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    const trimmed = userId.trim()
    if (!trimmed) return

    setLoading(true)
    setError('')

    try {
      const resp = await fetch('/api/auth/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: trimmed }),
      })

      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}: ${resp.statusText}`)
      }

      const data = await resp.json()
      localStorage.setItem('authToken', data.token)
      localStorage.setItem('userId', data.user_id)
      onLogin(trimmed)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-screen">
      <form className="login-form" onSubmit={handleSubmit}>
        <h2>Web Agent</h2>
        <input
          className="login-input"
          type="text"
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
          placeholder="Enter your user ID"
          autoFocus
          disabled={loading}
        />
        {error && <p className="login-error">{error}</p>}
        <button className="login-button" type="submit" disabled={loading || !userId.trim()}>
          {loading ? 'Logging in...' : 'Login'}
        </button>
      </form>
    </div>
  )
}

// Main App component (internal)
function MainApp() {
  const [userId, setUserId] = useState<string>(() => {
    return localStorage.getItem('userId') || 'default'
  })
  const [authToken, setAuthToken] = useState<string | null>(() => {
    return localStorage.getItem('authToken')
  })
  const [messages, setMessages] = useState<Message[]>([])
  const [sessions, setSessions] = useState<SessionItem[]>([])
  const [activeSession, setActiveSession] = useState<string | null>(() => {
    return localStorage.getItem('activeSession')
  })
  const activeSessionRef = useRef<string | null>(null)
  // Keep ref in sync so handleIncomingMessage doesn't need activeSession as a dep
  useEffect(() => {
    activeSessionRef.current = activeSession
  }, [activeSession])
  // Persist activeSession to localStorage
  useEffect(() => {
    if (activeSession) {
      localStorage.setItem('activeSession', activeSession)
    } else {
      localStorage.removeItem('activeSession')
    }
  }, [activeSession])
  const [sessionStates, setSessionStates] = useState<Map<string, string>>(new Map())

  // Per-session state setter — updates only the specified session
  const setSessionStateFor = useCallback((sessionId: string, state: string) => {
    setSessionStates(prev => {
      const next = new Map(prev)
      next.set(sessionId, state)
      return next
    })
  }, [])

  // Get the current active session's state (for InputBar disabled check)
  const activeSessionState = activeSession
    ? (sessionStates.get(activeSession) ?? 'idle')
    : 'idle'
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [filesOpen, setFilesOpen] = useState(false)
  const [fileCount, setFileCount] = useState<number>(0)
  const inputBarRef = useRef<InputBarHandle>(null)
  // Index threshold: messages with index >= this are "new turn" messages.
  // Use MAX_SAFE_INTEGER so only replay messages trigger the first-turn path.
  // Live messages (index < MAX) fall through to normal append logic.
  const clearThresholdRef = useRef<number>(Number.MAX_SAFE_INTEGER)
  // Tracks whether replay has started for the current turn.
  // If replay sends messages, we don't clear (replay already handles ordering).
  const replayStartedRef = useRef(false)

  // Click a file in a message bubble to reference it in the input
  const handleFileClick = useCallback((filename: string) => {
    inputBarRef.current?.insertText(`@${filename} `)
  }, [])

  // Keep a ref to messages count for accurate last_index (Step 1 + 4)
  const messagesRef = useRef(0)
  const firstMessageRef = useRef<string | null>(null)
  useEffect(() => {
    messagesRef.current = messages.length
    // Capture first user message for auto-title
    if (!firstMessageRef.current && messages.length > 0) {
      const firstUser = messages.find(m => m.type === 'user')
      if (firstUser) {
        firstMessageRef.current = firstUser.content.slice(0, 50)
      }
    }
  }, [messages])

  // Load sessions and file count from API
  useEffect(() => {
    loadSessions()
    loadFileCount()
  }, [userId])

  // Restore message history for the active session on mount (survives page refresh)
  useEffect(() => {
    if (activeSession) {
      // Load historical messages from backend
      const headers: Record<string, string> = {}
      if (authToken) headers['Authorization'] = `Bearer ${authToken}`
      fetch(`/api/users/${userId}/sessions/${activeSession}/history`, { headers })
        .then(resp => {
          if (resp.ok) return resp.json()
          return []
        })
        .then(data => {
          const msgs = (data as any[]).map((m: any) => ({
            ...m,
            // Use backend's absolute index; fallback to enumerate position
            index: m.index ?? -1,
            // Defensive: always ensure session_id for correct filtering
            session_id: activeSession,
          }))
          setMessages(msgs)
          // Derive sessionState from history
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
          setSessionStateFor(activeSession, derivedState)
          // Fetch live buffer state — may differ from persisted DB state
          fetch(`/api/users/${userId}/sessions/${activeSession}/status`, { headers })
            .then(resp => resp.json())
            .then(status => {
              if (status.state === 'running') {
                setSessionStateFor(activeSession, 'running')
              }
            })
            .catch(() => {})
        })
        .catch(() => {
          setMessages([])
          setSessionStateFor(activeSession, 'idle')
        })
    }
  }, [userId, authToken])

  const loadSessions = async () => {
    try {
      const headers: Record<string, string> = {}
      if (authToken) headers['Authorization'] = `Bearer ${authToken}`
      const resp = await fetch(`/api/users/${userId}/sessions`, { headers })
      if (resp.ok) {
        const data = await resp.json()
        setSessions(Array.isArray(data) ? data : [])
      }
    } catch {
      // Silently fail — sessions list is non-critical
    }
  }

  const loadFileCount = async () => {
    try {
      const resp = await fetch(`/api/users/${userId}/generated-files`)
      if (resp.ok) {
        const data = await resp.json()
        setFileCount(Array.isArray(data) ? data.length : 0)
      }
    } catch {
      setFileCount(0)
    }
  }

  const handleIncomingMessage = useCallback((msg: Message) => {
    const isInvisibleMessage =
      msg.type === 'heartbeat' ||
      (msg.type === 'system' && msg.subtype === 'session_state_changed')

    // Filter: skip messages from inactive sessions.
    // A single WebSocket receives messages from ALL sessions for this user.
    // Only display messages belonging to the currently active session.
    // Still process state changes (session_state_changed, result) for all sessions.
    if (msg.session_id && msg.session_id !== activeSessionRef.current) {
      if (msg.type === 'system' && msg.subtype === 'session_state_changed') {
        setSessionStateFor(msg.session_id, msg.state || msg.content || 'completed')
      }
      if (msg.type === 'result') {
        setSessionStateFor(msg.session_id, 'completed')
        loadSessions()
      }
      return
    }

    // Invisible messages from the active session: update state but don't append
    if (isInvisibleMessage) {
      if (msg.type === 'system' && msg.subtype === 'session_state_changed' && msg.session_id) {
        setSessionStateFor(msg.session_id, msg.state || msg.content || 'completed')
      }
      return
    }

    // Use a functional update so we always work with the latest `prev`.
    // This avoids stale-closure bugs and ensures dedup runs on every message.
    setMessages((prev) => {
      const isFirstTurnMessage =
        !replayStartedRef.current &&
        (msg.replay || msg.index >= clearThresholdRef.current)

      if (isFirstTurnMessage) {
        replayStartedRef.current = true
        // Append the incoming message to existing messages (which may
        // include recovered history). Dedup by index only.
        if (prev.some((m) => m.index === msg.index)) {
          return prev
        }
        return [...prev, msg]
      }

      // Non-first message: append with dedup.
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
      setActiveSession(msg.session_id)
    }

    if (msg.type === 'system' && msg.subtype === 'session_state_changed' && msg.session_id) {
      setSessionStateFor(msg.session_id, msg.state || msg.content || 'completed')
    }
    if (msg.type === 'result' && msg.session_id) {
      setSessionStateFor(msg.session_id, 'completed')
      // Auto-generate title from first message
      if (activeSessionRef.current && firstMessageRef.current) {
        fetch(`/api/users/${userId}/sessions/${activeSessionRef.current}/title`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: firstMessageRef.current }),
        }).catch(() => {})
      }
      loadSessions()
    }
    // Refresh file count from server when new files are generated
    // (cannot blindly increment because the agent may overwrite existing files)
    if (msg.type === 'file_result') {
      loadFileCount()
    }
  }, [userId])

  const { connected, sendMessage, sendAnswer, sendRecover } = useWebSocket({
    userId,
    onMessage: handleIncomingMessage,
    token: authToken ?? undefined,
  })

  // Auto-recover message history when WebSocket reconnects
  // Skip recovery on initial page load if REST already populated messages
  const didRecoverRef = useRef(false)
  useEffect(() => {
    if (connected && activeSessionRef.current && !didRecoverRef.current) {
      didRecoverRef.current = true
      sendRecover(activeSessionRef.current, 0)
    }
    // Reset recovery flag on disconnect so next reconnect can recover again
    if (!connected) {
      didRecoverRef.current = false
    }
  }, [connected, sendRecover])

  const handleSend = useCallback(
    async (message: string, files?: File[]) => {
      let sessionId = activeSessionRef.current

      // Auto-create session if none exists
      if (!sessionId) {
        try {
          const headers: Record<string, string> = {}
          if (authToken) headers['Authorization'] = `Bearer ${authToken}`
          const resp = await fetch(`/api/users/${userId}/sessions`, { method: 'POST', headers })
          const data = await resp.json()
          sessionId = data.session_id
          setActiveSession(sessionId)
          await loadSessions()
        } catch (err) {
          // Session creation failed — fall back to synthetic ID so UX isn't broken
          const errorMsg = err instanceof Error ? err.message : String(err)
          logger.error('Session creation failed, using synthetic ID', errorMsg)
          sessionId = `session_${userId}_${Date.now()}`
          setActiveSession(sessionId)
          setSessionStateFor(sessionId, 'error')
          setTimeout(() => setSessionStateFor(sessionId!, 'idle'), 3000)
        }
      }

      // Add user message immediately for UI responsiveness.
      // Use index = lastBackendIndex - 1 so it sorts BEFORE any replay
      // messages (which start at lastBackendIndex) but won't collide
      // with them during dedup.
      const lastBackendIndex = messagesRef.current
      // Set threshold: messages with index >= this are "new turn".
      // When first such message arrives, clear old messages.
      clearThresholdRef.current = lastBackendIndex
      replayStartedRef.current = false
      const fileMetadata = files?.map(f => ({ filename: f.name, size: f.size }))
      const optimisticMsg: Message = {
        type: 'user',
        content: message,
        index: lastBackendIndex - 1,
        data: fileMetadata,
      }
      setMessages((prev) => [...prev, optimisticMsg])
      setSessionStateFor(sessionId!, 'running')

      // last_index: number of messages the backend has already seen
      sendMessage({
        message,
        session_id: sessionId ?? undefined,
        last_index: lastBackendIndex,
        files: files?.map(f => f.name),
      })
    },
    [messagesRef, sendMessage, authToken, userId],
  )

  const handleNewSession = useCallback(async () => {
    setMessages([])
    setActiveSession(null)
    // Reset tracking refs — no active session means input should be enabled
    clearThresholdRef.current = Number.MAX_SAFE_INTEGER
    replayStartedRef.current = false
  }, [])

  const handleSelectSession = useCallback(async (id: string) => {
    setActiveSession(id)
    activeSessionRef.current = id  // Sync ref immediately — WS messages arriving
                                   // in the same tick must use the new session
    firstMessageRef.current = null
    // Reset tracking refs
    clearThresholdRef.current = Number.MAX_SAFE_INTEGER
    replayStartedRef.current = false

    // Load historical messages from backend
    try {
      const headers: Record<string, string> = {}
      if (authToken) headers['Authorization'] = `Bearer ${authToken}`
      const resp = await fetch(`/api/users/${userId}/sessions/${id}/history`, { headers })
      if (resp.ok) {
        const data = await resp.json()
        const msgs = (data as any[]).map((m: any) => ({
          ...m,
          index: m.index ?? -1,
          session_id: id,
        }))
        setMessages(msgs)
        // Restore first user message for title
        const firstUser = msgs.find((m: Message) => m.type === 'user')
        if (firstUser) firstMessageRef.current = firstUser.content.slice(0, 50)
        // Derive sessionState from the last session_state_changed message,
        // or fall back to 'idle' if none found.
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
        setSessionStateFor(id, derivedState)
        // After loading history, recover to catch up any live messages
        // from an active agent session (state may not yet be persisted)
        sendRecover(id, msgs.length)
        didRecoverRef.current = true  // Prevent auto-recovery from sending duplicate recover
        // Fetch live buffer state — the buffer may have session_state_changed
        // messages that haven't been flushed to DB yet (e.g., agent just started).
        fetch(`/api/users/${userId}/sessions/${id}/status`, { headers })
          .then(resp => resp.json())
          .then(status => {
            if (status.state === 'running') {
              setSessionStateFor(id, 'running')
            }
          })
          .catch(() => {})
      } else {
        setMessages([])
        setSessionStateFor(id, 'idle')
      }
    } catch {
      setMessages([])
      setSessionStateFor(id, 'idle')
    }
  }, [userId, authToken, setSessionStateFor, sendRecover])

  const handleDeleteSession = useCallback(async (id: string) => {
    if (!confirm('Delete this session?')) return
    try {
      const headers: Record<string, string> = {}
      if (authToken) headers['Authorization'] = `Bearer ${authToken}`
      const resp = await fetch(`/api/users/${userId}/sessions/${id}`, { method: 'DELETE', headers })
      if (!resp.ok) {
        throw new Error(`Failed to delete session (HTTP ${resp.status})`)
      }
      // Small delay to ensure filesystem sync before reload
      await new Promise(r => setTimeout(r, 200))
      // Refresh session list
      await loadSessions()
      // Clear if deleted the active session
      if (id === activeSession) {
        setMessages([])
        setActiveSession(null)
        // Clear this session's state from the map
        setSessionStates(prev => {
          const next = new Map(prev)
          next.delete(id)
          return next
        })
        // Reset replay tracking refs
        clearThresholdRef.current = Number.MAX_SAFE_INTEGER
        replayStartedRef.current = false
      }
    } catch (err) {
      logger.error('Failed to delete session', err)
      alert(err instanceof Error ? err.message : 'Failed to delete session')
    }
  }, [userId, authToken, activeSession])

  const handleLogout = useCallback(() => {
    localStorage.removeItem('authToken')
    localStorage.removeItem('userId')
    setAuthToken(null)
    setUserId('')
    setMessages([])
    setActiveSession(null)
    setSessions([])
  }, [])

  const stopSession = useCallback(async () => {
    if (!activeSession) return
    try {
      const headers: Record<string, string> = {}
      if (authToken) headers['Authorization'] = `Bearer ${authToken}`
      const resp = await fetch(`/api/users/${userId}/sessions/${activeSession}/cancel`, {
        method: 'POST',
        headers
      })
      if (resp.ok) {
        setSessionStateFor(activeSession, 'idle')
      }
    } catch (err) {
      console.error('Failed to stop session', err)
    }
  }, [activeSession, userId, authToken])

  // If no auth token, show login screen
  if (!authToken) {
    return <LoginScreen onLogin={(uid) => { setUserId(uid); setAuthToken(localStorage.getItem('authToken')) }} />
  }

  return (
    <div className="app">
      {/* Header */}
      <Header
        connected={connected}
        userId={userId}
        onOpenSettings={() => setSettingsOpen(true)}
        onLogout={handleLogout}
      />

      {/* Layout */}
      <div className="app-layout">
        <Sidebar
          sessions={sessions}
          activeSession={activeSession}
          onSelect={handleSelectSession}
          onNew={handleNewSession}
          onDelete={handleDeleteSession}
          onOpenFiles={() => setFilesOpen(true)}
          filesCount={fileCount}
        />
        <main className="main">
          <ChatArea
            messages={messages}
            sessionId={activeSession}
            sessionState={activeSessionState}
            onAnswer={sendAnswer}
            scrollPositions={sessionScrollPositions}
            onFileClick={handleFileClick}
          />
          <InputBar
            key={activeSession}
            ref={inputBarRef}
            onSend={handleSend}
            onStop={stopSession}
            disabled={!connected || activeSessionState === 'running'}
            userId={userId}
          />
        </main>
      </div>

      {/* Settings Overlay */}
      {settingsOpen && (
        <SettingsPanel
          authToken={authToken ?? ''}
          userId={userId}
          onClose={() => setSettingsOpen(false)}
        />
      )}
      {filesOpen && (
        <FilesPanel
          userId={userId}
          onClose={() => setFilesOpen(false)}
        />
      )}
    </div>
  )
}

export default function App() {
  // Design Preview Route - check before any hooks
  if (isDesignPreviewRoute()) {
    return <DesignPreviewPage />
  }

  // Settings Preview Route - check before any hooks
  if (isSettingsPreviewRoute()) {
    return <SettingsPreviewPage />
  }

  // Tech Preview Route - check before any hooks
  if (isTechPreviewRoute()) {
    return <TechPreviewPage />
  }

  return <MainApp />
}

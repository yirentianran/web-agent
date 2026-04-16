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

// Track scroll position per session (sessionId -> scrollTop)
const sessionScrollPositions = new Map<string, number>()

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
  const [activeSession, setActiveSession] = useState<string | null>(null)
  const activeSessionRef = useRef<string | null>(null)
  // Keep ref in sync so handleIncomingMessage doesn't need activeSession as a dep
  useEffect(() => {
    activeSessionRef.current = activeSession
  }, [activeSession])
  const [sessionState, setSessionState] = useState('idle')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [filesOpen, setFilesOpen] = useState(false)
  const [fileCount, setFileCount] = useState<number>(0)
  const inputBarRef = useRef<InputBarHandle>(null)

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
    setMessages((prev) => {
      if (msg.replay && prev.some((m) => m.index === msg.index)) {
        return prev
      }
      return [...prev, msg]
    })

    if (!activeSessionRef.current && msg.session_id) {
      setActiveSession(msg.session_id)
    }

    if (msg.type === 'system' && msg.subtype === 'session_state_changed') {
      setSessionState(msg.state || msg.content || 'completed')
    }
    if (msg.type === 'result') {
      setSessionState('completed')
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
  }, [userId])

  const { connected, sendMessage, sendAnswer } = useWebSocket({
    userId,
    onMessage: handleIncomingMessage,
    token: authToken ?? undefined,
  })

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
          setSessionState('error')
          setTimeout(() => setSessionState('idle'), 3000)
        }
      }

      // Add user message immediately for UI responsiveness
      const lastBackendIndex = messagesRef.current
      const fileMetadata = files?.map(f => ({ filename: f.name, size: f.size }))
      setMessages((prev) => [
        ...prev,
        { type: 'user', content: message, index: prev.length, data: fileMetadata },
      ])
      setSessionState('running')

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
    setSessionState('idle')
  }, [])

  const handleSelectSession = useCallback(async (id: string) => {
    setActiveSession(id)
    setSessionState('idle')
    firstMessageRef.current = null

    // Load historical messages from backend
    try {
      const headers: Record<string, string> = {}
      if (authToken) headers['Authorization'] = `Bearer ${authToken}`
      const resp = await fetch(`/api/users/${userId}/sessions/${id}/history`, { headers })
      if (resp.ok) {
        const data = await resp.json()
        const msgs = data.map((m: any, i: number) => ({ ...m, index: i }))
        setMessages(msgs)
        // Restore first user message for title
        const firstUser = msgs.find((m: Message) => m.type === 'user')
        if (firstUser) firstMessageRef.current = firstUser.content.slice(0, 50)
      } else {
        setMessages([])
      }
    } catch {
      setMessages([])
    }
  }, [userId, authToken])

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
        setSessionState('idle')
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
        setSessionState('idle')
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
            sessionState={sessionState}
            onAnswer={sendAnswer}
            scrollPositions={sessionScrollPositions}
            onFileClick={handleFileClick}
          />
          <InputBar
            ref={inputBarRef}
            onSend={handleSend}
            onStop={stopSession}
            disabled={!connected || sessionState === 'running'}
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

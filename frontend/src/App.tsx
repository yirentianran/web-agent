import {
  useState,
  useCallback,
  useEffect,
  useRef,
  type FormEvent,
} from "react";
import { Routes, Route, useNavigate, useMatch } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { generateUUID } from "./lib/uuid";
import Sidebar from "./components/Sidebar";
import Header from "./components/Header";
import ThemeToggle from "./components/ThemeToggle";
import LanguageSwitcher from "./i18n/LanguageSwitcher";
import ChatArea from "./components/ChatArea";
import InputBar, { type InputBarHandle } from "./components/InputBar";
import SkillsPage from "./components/SkillsPage";
import SessionFilePanel from "./components/SessionFilePanel";
import MemoryPanel from "./components/MemoryPanel";
import FeedbackPage from "./components/FeedbackPage";
import EvolutionPanel from "./components/EvolutionPanel";
import MCPPage from "./components/MCPPage";
import DesignPreviewPage from "./DesignPreviewPage";
import SettingsPreviewPage from "./SettingsPreviewPage";
import TechPreviewPage from "./TechPreviewPage";
import { useWebSocket } from "./hooks/useWebSocket";
import {
  useStreamingText,
  type StreamingTextState,
} from "./hooks/useStreamingText";
import type { Message, SessionItem, MessageSendState, ConnectionStatus, SessionStatus } from "./lib/types";
import {
  computeRecoverIndex,
  saveLastKnownIndex,
  loadLastKnownIndex,
  clearLastKnownIndex,
  savePendingMessage,
  clearPendingMessage,
  loadPendingMessage,
} from "./lib/session-state";
import { createLogger } from "./utils/logger";

const logger = createLogger("[App]");

// Valid session state transitions — used by setSessionStateFor to
// warn on unexpected transitions (soft check, no transition is blocked).
const VALID_TRANSITIONS: Record<string, SessionStatus[]> = {
  idle: ["running"],
  running: ["completed", "error", "cancelled", "idle"],
  completed: ["running"],
  error: ["running", "idle"],
  cancelled: ["running", "idle"],
};

// Persist scroll position to localStorage so it survives page refresh
const SCROLL_STORAGE_KEY = "web-agent-scroll-positions";

function loadScrollPositions(): Map<string, number> {
  try {
    const raw = localStorage.getItem(SCROLL_STORAGE_KEY);
    if (!raw) return new Map();
    const parsed = JSON.parse(raw) as [string, number][];
    return new Map(parsed);
  } catch {
    return new Map();
  }
}

const sessionScrollPositions = loadScrollPositions();

interface LoginScreenProps {
  onLogin: (userId: string) => void;
}

function LoginScreen({ onLogin }: LoginScreenProps) {
  const { t } = useTranslation();
  const [userId, setUserId] = useState("");
  const [password, setPassword] = useState("");
  const [enforceAuth, setEnforceAuth] = useState(true);
  const [configLoaded, setConfigLoaded] = useState(false);
  const [isRegister, setIsRegister] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetch("/api/auth/config")
      .then((r) => r.json())
      .then((cfg: { enforce_auth: boolean }) => {
        setEnforceAuth(cfg.enforce_auth);
        setConfigLoaded(true);
      })
      .catch(() => setConfigLoaded(true));
  }, []);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = userId.trim();
    if (!trimmed) return;

    setLoading(true);
    setError("");

    try {
      const endpoint = isRegister ? "/api/auth/register" : "/api/auth/token";
      const resp = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: trimmed, password }),
      });

      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${resp.status}`);
      }

      const data = await resp.json();
      localStorage.setItem("authToken", data.token);
      localStorage.setItem("userId", data.user_id);
      onLogin(trimmed);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('login.error'));
    } finally {
      setLoading(false);
    }
  };

  if (!configLoaded) return null;

  return (
    <div className="login-screen">
      <header className="app-header login-header">
        <div className="app-brand">
          <span className="app-logo">◎</span>
          <span className="app-name">{t('header.brandName')}</span>
        </div>
        <div className="app-header-actions">
          <LanguageSwitcher />
          <ThemeToggle />
        </div>
      </header>
      <form className="login-form" onSubmit={handleSubmit}>
        <h2>{isRegister ? t('login.registerTitle') : t('login.title')}</h2>
        <input
          className="login-input"
          type="text"
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
          placeholder={t('login.placeholder')}
          autoFocus
          disabled={loading}
        />
        {enforceAuth && (
          <input
            className="login-input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={t('login.passwordPlaceholder')}
            disabled={loading}
          />
        )}
        {error && <p className="login-error">{error}</p>}
        <button
          className="login-button"
          type="submit"
          disabled={loading || !userId.trim()}
        >
          {loading
            ? t('login.submittingButton')
            : isRegister
              ? t('login.registerButton')
              : t('login.submitButton')}
        </button>
        {enforceAuth && (
          <button
            type="button"
            className="login-toggle"
            onClick={() => { setIsRegister(!isRegister); setError(""); }}
            disabled={loading}
          >
            {isRegister ? t('login.switchToLogin') : t('login.switchToRegister')}
          </button>
        )}
      </form>
    </div>
  );
}

// Main App component (internal)
interface MainLayoutProps {
  status: string;
  queueFull: boolean;
  userId: string;
  sidebarOpen: boolean;
  setSidebarOpen: (v: boolean | ((v: boolean) => boolean)) => void;
  sessions: SessionItem[];
  activeSession: string | null;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSession: (id: string) => Promise<void>;
  onRenameSession: (id: string, title: string) => Promise<void>;
  messages: Message[];
  activeSessionStatus: SessionStatus;
  sendAnswer: (sessionId: string, answers: Record<string, string>) => void;
  handleFileClick: (filename: string) => void;
  handleResend: (message: Message) => void;
  authToken: string | null;
  streamingText: string;
  inputBarRef: React.RefObject<InputBarHandle | null>;
  handleSend: (message: string, files?: File[]) => Promise<void>;
  stopSession: () => Promise<void>;
  filePanelOpen: boolean;
  setFilePanelOpen: (v: boolean | ((v: boolean) => boolean)) => void;
  fileRefreshKey: number;
  setFileRefreshKey: (v: number | ((v: number) => number)) => void;
  handleLogout: () => void;
  navigate: ReturnType<typeof useNavigate>;
  sessionLoading: boolean;
  sessionCreateError: boolean;
  setSessionCreateError: (v: boolean | ((v: boolean) => boolean)) => void;
  userRole: string;
}

function MainLayout({
  status,
  queueFull,
  userId,
  sidebarOpen,
  setSidebarOpen,
  sessions,
  activeSession,
  onSelectSession,
  onNewSession,
  onDeleteSession,
  onRenameSession,
  messages,
  activeSessionStatus,
  sendAnswer,
  handleFileClick,
  handleResend,
  authToken,
  streamingText,
  inputBarRef,
  handleSend,
  stopSession,
  filePanelOpen,
  setFilePanelOpen,
  fileRefreshKey,
  setFileRefreshKey,
  handleLogout,
  navigate,
  sessionLoading,
  sessionCreateError,
  setSessionCreateError,
  userRole,
}: MainLayoutProps) {
  const { t } = useTranslation();
  return (
    <div className="app">
      {/* Reconnection failure banner */}
      {status === "failed" && (
        <div className="connection-banner connection-banner--failed">
          <span>{t('connection.lostBanner')}</span>
          <button onClick={() => window.location.reload()}>{t('connection.refreshPage')}</button>
        </div>
      )}
      {/* Reconnecting indicator */}
      {status === "reconnecting" && (
        <div className="connection-banner connection-banner--reconnecting">
          <span>{t('connection.reconnectingBanner')}</span>
        </div>
      )}
      {/* Header */}
      <Header
        connectionStatus={status as ConnectionStatus}
        userId={userId}
        authToken={authToken}
        onOpenSkills={() => navigate("/skills")}
        onOpenFeedback={() => navigate("/feedback")}
        onOpenEvolution={() => navigate("/evolution")}
        onOpenMCP={() => navigate("/mcp")}
        onOpenMemory={() => navigate("/memory")}
        onLogout={handleLogout}
        userRole={userRole}
      />

      {/* Layout */}
      <div className="app-layout">
        <div className={`sidebar-wrapper ${sidebarOpen ? 'open' : ''}`}>
          <button
            className="sidebar-toggle"
            onClick={() => setSidebarOpen(v => !v)}
            title={sidebarOpen ? t('sidebar.collapseSidebar') : t('sidebar.expandSidebar')}
            type="button"
          >
            <span className="sidebar-toggle-icon">{sidebarOpen ? '\u25C2' : '\u25B8'}</span>
          </button>
          <Sidebar
            sessions={sessions}
            activeSession={activeSession}
            onSelect={onSelectSession}
            onNew={onNewSession}
            onDelete={onDeleteSession}
            onRename={onRenameSession}
          />
        </div>
        <main className="main">
          {sessionCreateError && (
            <div className="connection-banner connection-banner--failed">
              <span>{t('chat.sessionCreateFailed')}</span>
              <button onClick={() => setSessionCreateError(false)}>Dismiss</button>
            </div>
          )}
          {queueFull && (
            <div
              style={{
                background: "#fff3cd",
                color: "#856404",
                padding: "8px 16px",
                fontSize: "0.85rem",
                textAlign: "center",
                borderBottom: "1px solid #ffc107",
              }}
            >
              {t('connection.slowBanner')}
            </div>
          )}
          <ChatArea
            messages={messages}
            sessionId={activeSession}
            sessionState={activeSessionStatus}
            onAnswer={sendAnswer}
            scrollPositions={sessionScrollPositions}
            onFileClick={handleFileClick}
            onResend={handleResend}
            authToken={authToken}
            streamingText={streamingText}
            sessionLoading={sessionLoading}
          />
          <InputBar
            key={activeSession}
            ref={inputBarRef}
            onSend={handleSend}
            onStop={stopSession}
            disabled={status !== "connected" || activeSessionStatus === "running"}
            isRunning={activeSessionStatus === "running" && status === "connected"}
            userId={userId}
            authToken={authToken || undefined}
          />
        </main>
        <div className={`file-panel-wrapper ${filePanelOpen ? 'open' : ''}`}>
          <button
            className="file-panel-toggle"
            onClick={() => {
              setFilePanelOpen(v => {
                if (!v) setFileRefreshKey(k => k + 1);
                return !v;
              });
            }}
            title={filePanelOpen ? t('filePanel.collapseFiles') : t('filePanel.expandFiles')}
            type="button"
          >
            <span className="file-panel-toggle-icon">{filePanelOpen ? '\u25B8' : '\u25C2'}</span>
          </button>
          <SessionFilePanel
            userId={userId}
            authToken={authToken}
            activeSessionId={activeSession}
            onFileClick={handleFileClick}
            refreshKey={fileRefreshKey}
          />
        </div>
      </div>
    </div>
  );
}

function MainApp() {
  const { t } = useTranslation();
  const [userId, setUserId] = useState<string>(() => {
    return localStorage.getItem("userId") || "";
  });
  const [authToken, setAuthToken] = useState<string | null>(() => {
    return localStorage.getItem("authToken");
  });
  const [userRole, setUserRole] = useState<string>(() => {
    const token = localStorage.getItem("authToken");
    if (!token) return "user";
    try {
      const payload = JSON.parse(atob(token.split(".")[1]));
      return payload.role || "user";
    } catch {
      return "user";
    }
  });
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  // urlSessionId is the single source of truth for the active session,
  // derived from the URL via useMatch below. The ref keeps it current
  // for callbacks that can't depend on the derived value directly.
  const sessionMatch = useMatch("/chat/:sessionId");
  const urlSessionId = sessionMatch?.params.sessionId ?? null;
  const urlSessionIdRef = useRef<string | null>(urlSessionId);
  useEffect(() => {
    urlSessionIdRef.current = urlSessionId;
  }, [urlSessionId]);
  const [sessionStates, setSessionStatuss] = useState<Map<string, string>>(
    new Map(),
  );

  // Streaming text aggregation state — accumulates content_block_delta events
  const [streamingTextState, setStreamingTextState] =
    useState<StreamingTextState>(useStreamingText.createInitialState());
  const streamingTextStateRef = useRef<StreamingTextState>(streamingTextState);
  // Keep ref in sync for handleIncomingMessage
  useEffect(() => {
    streamingTextStateRef.current = streamingTextState;
  }, [streamingTextState]);

  // Per-session state setter — the single entry point for all session state
  // changes. Also syncs to sessionStatesRef to avoid stale closure bugs in
  // handleIncomingMessage when WebSocket messages arrive between
  // React scheduling a state update and applying it.
  const setSessionStateFor = useCallback((sessionId: string, newState: SessionStatus) => {
    const prevState = sessionStatesRef.current.get(sessionId);
    if (prevState === newState) return; // no-op: same state
    const allowed = VALID_TRANSITIONS[prevState ?? ""];
    if (allowed && !allowed.includes(newState)) {
      logger.warn(
        `Unexpected session state transition: ${prevState} → ${newState} (session: ${sessionId})`,
      );
    }
    sessionStatesRef.current.set(sessionId, newState);
    setSessionStatuss((prev) => {
      const next = new Map(prev);
      next.set(sessionId, newState);
      return next;
    });
  }, []);

  // Get the current active session's state (for InputBar disabled check)
  const activeSessionStatus: SessionStatus = urlSessionId
    ? (sessionStates.get(urlSessionId) as SessionStatus | undefined ?? "idle")
    : "idle";
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [filePanelOpen, setFilePanelOpen] = useState(false);
  const [fileRefreshKey, setFileRefreshKey] = useState(0);
  const [sessionLoading, setSessionLoading] = useState(false);
  const [sessionCreateError, setSessionCreateError] = useState(false);
  const inputBarRef = useRef<InputBarHandle>(null);
  const navigate = useNavigate();
  // Index threshold: messages with index >= this are "new turn" messages.
  // Use MAX_SAFE_INTEGER so only replay messages trigger the first-turn path.
  // Live messages (index < MAX) fall through to normal append logic.
  const clearThresholdRef = useRef<number>(Number.MAX_SAFE_INTEGER);
  // Tracks whether replay has started for the current turn.
  // If replay sends messages, we don't clear (replay already handles ordering).
  const replayStartedRef = useRef(false);
  // Tracks whether REST history fetch has completed.
  // Used to coordinate auto-recover: we wait for REST before sending
  // recover so setMessages(msgs) doesn't wipe WebSocket-added messages.
  const restLoadedRef = useRef(false);
  // When true, block the auto-activate in handleIncomingMessage (line 767).
  // Set by handleNewSession, cleared by handleSend when a real session starts.
  const suppressAutoActivateRef = useRef(false);

  // Pending user messages per session — tracks messages sent via WebSocket
  // but not yet confirmed by the backend. When switching sessions, pending
  // messages are preserved so they survive the setMessages() replacement.
  // When switching back to a session with pending messages, they are restored
  // immediately so the user sees their message even if the backend hasn't
  // received the WebSocket message yet.
  const pendingUserMsgsRef = useRef<Map<string, Message>>(new Map());

  // Click a file in a message bubble to reference it in the input
  const handleFileClick = useCallback((filename: string) => {
    inputBarRef.current?.insertText(`@${filename} `);
  }, []);

  // Track highest message index for accurate last_index and optimistic message ordering
  const messagesRef = useRef(0);
  // Track the highest message index across all received messages.
  // Used to assign a valid index to optimistic user messages so they
  // sort after all existing messages, not before them.
  const maxMsgIndexRef = useRef(0);
  const firstMessageRef = useRef<string | null>(null);
  useEffect(() => {
    messagesRef.current = messages.length;
    // Update maxMsgIndex from the actual message indices (not array length)
    let maxIdx = 0;
    for (const m of messages) {
      if (m.index != null && m.index > maxIdx) maxIdx = m.index;
    }
    maxMsgIndexRef.current = maxIdx;
    // Capture first user message for auto-title
    if (!firstMessageRef.current && messages.length > 0) {
      const firstUser = messages.find((m) => m.type === "user");
      if (firstUser) {
        firstMessageRef.current = firstUser.content.slice(0, 50);
      }
    }
  }, [messages]);

  // Load sessions and file count from API
  useEffect(() => {
    loadSessions();
  }, [userId]);

  // Restore message history for the active session on mount (survives page refresh)
  useEffect(() => {
    if (urlSessionId) {
      setSessionLoading(true);

      // Restore pending message from localStorage so the user's message
      // survives a page refresh even if the WebSocket message hasn't been
      // processed by the server yet.
      const storedPending = loadPendingMessage(urlSessionId, userId);
      if (storedPending) {
        // Use lastKnownIndex + 1 so the pending message sorts at the bottom
        // instead of at the top (which would happen with index=-1).
        const lastKnownIdx = loadLastKnownIndex(urlSessionId, userId);
        const syntheticIndex = lastKnownIdx >= 0 ? lastKnownIdx + 1 : 0;
        const optimisticMsg: Message = {
          type: "user",
          content: storedPending.content,
          index: syntheticIndex,
          data: storedPending.files,
          clientMsgId: storedPending.clientMsgId,
          sendState: "sending",
          session_id: urlSessionId,
        };
        pendingUserMsgsRef.current.set(urlSessionId, optimisticMsg);
        sendStateMapRef.current.set(storedPending.clientMsgId, "sending");
        setMessages((prev) => {
          const exists = prev.some(
            (m) => m.clientMsgId === storedPending.clientMsgId,
          );
          if (exists) return prev;
          return [...prev, optimisticMsg];
        });
        // Set state to running — the user sent a message and the agent
        // should be working on it. REST /history and /status will correct
        // the state if the message wasn't actually processed yet.
        setSessionStateFor(urlSessionId, "running");
      }

      // Load historical messages from backend
      const headers: Record<string, string> = {};
      const token = authTokenRef.current;
      if (token) headers["Authorization"] = `Bearer ${token}`;
      fetch(`/api/users/${userId}/sessions/${urlSessionId}/history`, {
        headers,
      })
        .then((resp) => {
          logger.debug(
            "[REST /history] status=%d ok=%s userId=%s sessionId=%s",
            resp.status, resp.ok, userId, urlSessionId,
          );
          if (resp.status === 403 || resp.status === 404) {
            window.location.href = window.location.origin;
            throw new Error("Permission denied");
          }
          if (resp.ok) return resp.json();
          return [];
        })
        .then((data) => {
          logger.debug(
            "[REST /history] data received: %d messages, session=%s",
            Array.isArray(data) ? data.length : -1, urlSessionId,
          );
          // Guard against stale fetch: if user switched sessions,
          // the ref will point to a different session
          if (urlSessionIdRef.current !== urlSessionId) return;
          const msgs = (data as any[]).map((m: any) => ({
            ...m,
            index: m.index ?? -1,
            session_id: urlSessionId,
          }));
          setMessages((prev) => {
            logger.debug(
              "[setMessages] prev=%d msgs (prev[0].session=%s) new=%d msgs (session=%s)",
              prev.length,
              prev.length > 0 ? prev[0].session_id : "none",
              msgs.length,
              urlSessionId,
            );
            // Keep only messages belonging to the current session — when
            // switching sessions, prev holds the old session's messages.
            const sameSession = prev.filter(
              (m) => m.session_id === urlSessionId,
            );
            if (sameSession.length === 0) {
              logger.debug("[setMessages] no same-session msgs, replacing with %d new msgs", msgs.length);
              return msgs;
            }
            const prevIndices = new Set(sameSession.map((m) => m.index));
            const prevClientMsgIds = new Set(
              sameSession.filter((m) => m.clientMsgId).map((m) => m.clientMsgId),
            );
            const newMsgs = msgs.filter(
              (m: Message) =>
                !prevIndices.has(m.index) &&
                !(m.clientMsgId && prevClientMsgIds.has(m.clientMsgId)),
            );
            if (newMsgs.length === 0) return sameSession;
            return [...sameSession, ...newMsgs].sort(
              (a, b) => (a.index ?? 0) - (b.index ?? 0),
            );
          });
          restLoadedRef.current = true;
          let derivedState: SessionStatus = "idle";
          for (let i = msgs.length - 1; i >= 0; i--) {
            const m = msgs[i];
            if (
              m.type === "system" &&
              m.subtype === "session_state_changed" &&
              m.state
            ) {
              derivedState = m.state as SessionStatus;
              break;
            }
            if (m.type === "result") {
              derivedState = "completed";
              break;
            }
          }
          const currentState = sessionStatesRef.current.get(urlSessionId) ?? "idle";
          if (currentState === "running" && derivedState !== "running") {
            // Preserve live "running" — don't downgrade to "idle"/"completed"
            // from stale history. The WebSocket will deliver the correct state.
          } else {
            setSessionStateFor(urlSessionId, derivedState);
          }
          fetch(`/api/users/${userId}/sessions/${urlSessionId}/status`, {
            headers,
          })
            .then((resp) => {
              if (resp.status === 403 || resp.status === 404) {
                window.location.href = window.location.origin;
                throw new Error("Permission denied");
              }
              return resp.json();
            })
            .then((status) => {
              if (urlSessionIdRef.current !== urlSessionId) return;
              if (status.state === "running" && (status.buffer_age ?? 0) < 30) {
                setSessionStateFor(urlSessionId, "running");
              } else if (
                status.state === "running" &&
                (status.buffer_age ?? 0) >= 30
              ) {
                sendRecover(
                  urlSessionId!,
                  msgs.length > 0
                    ? computeRecoverIndex(msgs as unknown as Message[])
                    : 0,
                );
                didRecoverRef.current = true;
              }
            })
            .catch(() => {})
            .finally(() => { setSessionLoading(false); });
        })
        .catch(() => {
          setSessionLoading(false);
          window.location.href = window.location.origin;
        });
    }
  }, [userId, urlSessionId]);

  const loadSessions = async () => {
    if (!userId) return;
    try {
      const headers: Record<string, string> = {};
      if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
      const resp = await fetch(`/api/users/${userId}/sessions`, { headers });
      if (resp.status === 403) {
        window.location.href = window.location.origin;
        return;
      }
      if (resp.ok) {
        const data = await resp.json();
        setSessions(Array.isArray(data) ? data : []);
      }
    } catch {
      // Silently fail — sessions list is non-critical
    }
  };

  // Track last heartbeat arrival — used for staleness detection.
  // When the backend subscribe loop exits (agent done), heartbeats stop.
  // If WS is connected but no heartbeat for 60s while 'running',
  // the completion signal was likely lost → trigger recovery.
  const lastHeartbeatRef = useRef(Date.now());

  // Reset heartbeat ref on session switch to prevent false staleness
  // (old heartbeat from previous session could be >60s ago)
  useEffect(() => {
    lastHeartbeatRef.current = Date.now();
  }, [urlSessionId]);

  // Helper: update send state for a message by clientMsgId
  const updateSendState = useCallback(
    (clientMsgId: string | undefined, newState: MessageSendState) => {
      if (!clientMsgId) return;
      sendStateMapRef.current.set(clientMsgId, newState);
      // Update the corresponding message in the messages array
      setMessages((prev) =>
        prev.map((m) =>
          m.clientMsgId === clientMsgId ? { ...m, sendState: newState } : m,
        ),
      );
    },
    [],
  );

  const handleIncomingMessage = useCallback(
    (msg: Message) => {
      // Log incoming WS messages for debugging cross-user access
      if (msg.type !== "heartbeat") {
        logger.debug(
          "[WS incoming] type=%s subtype=%s session=%s index=%d replay=%s",
          msg.type, msg.subtype || "-", msg.session_id || "-", msg.index ?? -1, msg.replay ?? false,
        );
      }
      // Normalize snake_case fields from Python server to camelCase.
      // Without this, dedup logic that checks clientMsgId silently fails
      // because the server sends client_msg_id (snake_case) while the
      // TypeScript interface expects clientMsgId (camelCase).
      const raw = msg as unknown as Record<string, unknown>;
      if (raw.client_msg_id && !msg.clientMsgId) {
        msg.clientMsgId = raw.client_msg_id as string;
      }

      // Handle auth failure — clear token and force re-login
      if (msg.type === "auth_error") {
        localStorage.removeItem("authToken");
        localStorage.removeItem("userId");
        setAuthToken(null);
        return;
      }

      const TERMINAL_STATES = new Set(["completed", "error", "cancelled"]);

      // Track heartbeat for staleness detection
      if (msg.type === "heartbeat") {
        lastHeartbeatRef.current = Date.now();
        // Agent task no longer exists — trigger immediate recovery
        // instead of waiting for 60s staleness timeout
        if (msg.agent_alive === false && urlSessionIdRef.current) {
          sendRecoverRef.current(urlSessionIdRef.current, messages.length);
        }
      }

      // Process streaming text from content_block_delta events
      // Aggregate text deltas into a single streaming state for display
      const newStreamingState = useStreamingText.processMessage(
        streamingTextStateRef.current,
        msg,
      );
      if (newStreamingState !== streamingTextStateRef.current) {
        streamingTextStateRef.current = newStreamingState;
        setStreamingTextState(newStreamingState);
      }

      // Update last_known_index for persistence
      if (
        msg.type !== "heartbeat" &&
        msg.type !== "system" &&
        msg.index != null &&
        msg.index >= 0
      ) {
        const sid = msg.session_id || urlSessionIdRef.current;
        if (sid) {
          saveLastKnownIndex(sid, msg.index, userId);
        }
      }

      // Track highest user message index — used to filter out old state changes
      // from previous runs. A state change with index lower than the latest
      // user message belongs to a previous run (the subscribe loop sends old
      // state changes with replay: False, bypassing replay protection).
      if (
        msg.type === "user" &&
        msg.index != null &&
        msg.index > highestUserMsgIndexRef.current
      ) {
        highestUserMsgIndexRef.current = msg.index;
      }

      // Backend confirmed user message echo: clear pending and confirm send
      // Match by clientMsgId first, then fallback to content match (backward compat)
      if (msg.type === "user" && msg.session_id) {
        const pending = pendingUserMsgsRef.current.get(msg.session_id);
        const matchedByUuid =
          msg.clientMsgId && pending?.clientMsgId === msg.clientMsgId;
        const matchedByContent =
          !msg.replay && !msg.clientMsgId && pending && pending.content === msg.content;
        if (matchedByUuid || matchedByContent) {
          pendingUserMsgsRef.current.delete(msg.session_id);
            clearPendingMessage(msg.session_id, userId);
          if (pending?.clientMsgId) {
            updateSendState(pending.clientMsgId, "sent");
            confirmSendRef.current(pending.clientMsgId);
          }
        }
      }

      // Also confirm send if backend echoes a user message we're tracking (by clientMsgId on the incoming msg)
      if (msg.type === "user" && msg.clientMsgId) {
        updateSendState(msg.clientMsgId, "sent");
        confirmSendRef.current(msg.clientMsgId);
      }

      const isInvisibleMessage =
        msg.type === "heartbeat" ||
        (msg.type === "system" && msg.subtype === "session_state_changed");

      // Filter: skip messages from inactive sessions.
      // A single WebSocket receives messages from ALL sessions for this user.
      // Only display messages belonging to the currently active session.
      // Still process state changes (session_state_changed, result) for all sessions.
      if (msg.session_id && msg.session_id !== urlSessionIdRef.current) {
        if (msg.type === "system" && msg.subtype === "session_state_changed") {
          const newState = (msg.state || msg.content || "completed") as SessionStatus;
          // Index-based filtering: block state changes from previous runs
          if (msg.index != null && msg.index < highestUserMsgIndexRef.current) {
            // Skip — this state change is older than the current run's user message
          } else if (msg.replay) {
            const currentState = sessionStatesRef.current.get(msg.session_id);
            const isTerminal = TERMINAL_STATES.has(newState);
            if (isTerminal) {
              // Replay terminal states are authoritative (from DB)
              setSessionStateFor(msg.session_id, newState);
            } else if (currentState === "running") {
              // Skip — live running state takes precedence over replayed non-terminal
            } else {
              setSessionStateFor(msg.session_id, newState);
            }
          } else {
            setSessionStateFor(msg.session_id, newState);
          }
        }
        if (msg.type === "result") {
          // Guard against old result messages from previous runs
          if (
            msg.index == null ||
            msg.index >= highestUserMsgIndexRef.current
          ) {
            setSessionStateFor(msg.session_id, "completed");
            loadSessions();
          }
        }
        return;
      }

      // Invisible messages from the active session: update state but don't append
      if (isInvisibleMessage) {
        if (
          msg.type === "system" &&
          msg.subtype === "session_state_changed" &&
          msg.session_id
        ) {
          const newState = (msg.state || msg.content || "completed") as SessionStatus;
          const isTerminal = TERMINAL_STATES.has(newState);
          // Accept terminal state changes even if index is slightly lower,
          // but never overwrite a live 'running' state with an old terminal.
          if (msg.index != null && msg.index < highestUserMsgIndexRef.current) {
            if (!isTerminal) {
              // Skip — old non-terminal state change
            } else {
              const currentState = sessionStatesRef.current.get(msg.session_id);
              if (currentState !== "running") {
                setSessionStateFor(msg.session_id, newState);
              }
            }
          } else if (msg.replay) {
            const currentState = sessionStatesRef.current.get(msg.session_id);
            const isTerminal = TERMINAL_STATES.has(newState);
            if (isTerminal) {
              // Replay terminal states are authoritative (from DB)
              setSessionStateFor(msg.session_id, newState);
            } else if (currentState === "running") {
              // Skip — live running state takes precedence over replayed non-terminal
            } else if (newState === "running" && currentState !== "running") {
              // Skip — replayed "running" is stale history;
              // if the agent were truly running we'd get a live message
            } else {
              setSessionStateFor(msg.session_id, newState);
            }
          } else {
            // Live (non-replay) message for the active session.
            // Live session_state_changed messages from the subscribe loop
            // are always current-run signals — the loop starts from
            // last_seen which is beyond any previous-run messages.
            // Old state changes only arrive in the initial replay (handled
            // above in the replay branch).
            setSessionStateFor(msg.session_id, newState);
          }
          // Refresh file panel on session state changes (files may have been generated)
          setFileRefreshKey(k => k + 1);
        }
        return;
      }

      // Any non-heartbeat message for the active session confirms
      // the recover channel is working — clear the recover timeout.
      if (msg.session_id) {
        confirmRecoverRef.current(msg.session_id);
      }

      // Use a functional update so we always work with the latest `prev`.
      // This avoids stale-closure bugs and ensures dedup runs on every message.
      setMessages((prev) => {
        const isFirstTurnMessage =
          !replayStartedRef.current &&
          (msg.replay || msg.index >= clearThresholdRef.current);

        let next: Message[];
        let dedupResult = "append";  // track dedup outcome for logging

        if (isFirstTurnMessage) {
          replayStartedRef.current = true;
          if (prev.some((m) => m.index === msg.index)) {
            next = prev;
            dedupResult = "skip:firstTurn-indexDup";
          } else if (msg.type === "user" && !msg.replay) {
            if (
              msg.clientMsgId &&
              prev.some((m) => m.clientMsgId === msg.clientMsgId)
            ) {
              next = prev;
              dedupResult = "skip:firstTurn-user-clientMsgIdDup";
            } else if (
              prev.some(
                (m) => m.type === "user" && m.content === msg.content,
              )
            ) {
              next = prev;
              dedupResult = "skip:firstTurn-user-contentDup";
            } else {
              next = [...prev, msg];
              dedupResult = "append:firstTurn-user";
            }
          } else {
            next = [...prev, msg];
            dedupResult = "append:firstTurn-other";
          }
        } else if (msg.replay && prev.some((m) => m.index === msg.index)) {
          next = prev;
          dedupResult = "skip:replay-indexDup";
        } else if (msg.type === "user" && !msg.replay) {
          if (
            msg.clientMsgId &&
            prev.some((m) => m.clientMsgId === msg.clientMsgId)
          ) {
            next = prev;
            dedupResult = "skip:user-clientMsgIdDup";
          } else if (
            !msg.clientMsgId &&
            prev.some((m) => m.type === "user" && m.content === msg.content)
          ) {
            next = prev;
            dedupResult = "skip:user-contentDup";
          } else {
            next = [...prev, msg];
            dedupResult = "append:user";
          }
        } else if (!msg.replay && msg.type !== "user") {
          if (msg.index != null && prev.some((m) => m.index === msg.index)) {
            next = prev;
            dedupResult = "skip:nonReplay-indexDup";
          } else {
            next = [...prev, msg];
            dedupResult = "append:nonReplay-nonUser";
          }
        } else {
          next = [...prev, msg];
          dedupResult = "append:fallthrough";
        }

        // Restore send states from the source-of-truth map. This
        // ensures the UI reflects the real state regardless of
        // whether the optimistic insert or the WebSocket echo
        // was processed first.
        let sendStateChanged = false;
        const withStates = next.map((m) => {
          if (!m.clientMsgId) return m;
          const state = sendStateMapRef.current.get(m.clientMsgId);
          if (state && m.sendState !== state) {
            sendStateChanged = true;
            return { ...m, sendState: state };
          }
          return m;
        });

        // ── DIAGNOSTIC LOGGING ──────────────────────────────
        const assistantIndices = next
          .filter(m => m.type === "assistant")
          .map(m => m.index)
          .join(",");
        logger.debug(
          "[setMessages] type=%s subtype=%s idx=%d replay=%s firstTurn=%s result=%s prevLen=%d nextLen=%d assistants=[%s] clearThresh=%d replayStarted=%s",
          msg.type,
          msg.subtype || "-",
          msg.index ?? -1,
          msg.replay ?? false,
          isFirstTurnMessage,
          dedupResult,
          prev.length,
          next.length,
          assistantIndices,
          clearThresholdRef.current,
          replayStartedRef.current,
        );

        // Update maxMsgIndexRef synchronously so handleSend always
        // reads the latest value, avoiding stale last_index on send.
        let maxIdx = maxMsgIndexRef.current;
        for (const m of withStates) {
          if (m.index != null && m.index > maxIdx) maxIdx = m.index;
        }
        maxMsgIndexRef.current = maxIdx;

        return sendStateChanged ? withStates : next;
      });

      // Trigger file panel refresh when files are generated or session state changes
      if (
        msg.type === 'file_upload' ||
        msg.type === 'file_result' ||
        (msg.type === 'system' && msg.subtype === 'session_state_changed')
      ) {
        setFileRefreshKey(k => k + 1);
      }

      if (!urlSessionIdRef.current && msg.session_id && !suppressAutoActivateRef.current) {
        navigate("/chat/" + msg.session_id);
      }

      if (
        msg.type === "system" &&
        msg.subtype === "session_state_changed" &&
        msg.session_id
      ) {
        const newState = (msg.state || msg.content || "completed") as SessionStatus;
        // Live session_state_changed — accept it (subscribe loop
        // only sends current-run messages after last_seen).
        setSessionStateFor(
          msg.session_id,
          newState,
        );
      }
      if (msg.type === "result" && msg.session_id) {
        // result is always terminal — accept it. The last_index
        // parameter sent to the backend ensures only current-run
        // results reach the frontend (old results are filtered by
        // after_index on the backend side).
        setSessionStateFor(msg.session_id, "completed");
        loadSessions();
      }
    },
    [userId, updateSendState],
  );

  // Refs to break circular dependency between handleIncomingMessage and useWebSocket
  const confirmSendRef = useRef<(clientMsgId: string) => void>(() => {});
  const sendRecoverRef = useRef<(sessionId: string, afterIndex: number) => void>(() => {});
  const confirmRecoverRef = useRef<(sessionId: string) => void>(() => {});
  const sendStateMapRef = useRef<Map<string, MessageSendState>>(new Map());
  // Ref for authToken so session-loading effect doesn't re-fire on token changes
  const authTokenRef = useRef(authToken);
  authTokenRef.current = authToken;
  // Mirror of sessionStates for use in handleIncomingMessage — avoids
  // stale closure bugs when WebSocket messages arrive between React
  // scheduling a state update and applying it (the "spinner disappears
  // after send" bug).
  const sessionStatesRef = useRef<Map<string, string>>(new Map());
  // Track the highest index of any user message received. Used to filter
  // out old state changes from previous runs — they have lower indices
  // than the user message that started the current run. The subscribe
  // loop sends old state changes with replay: False, so replay protection
  // doesn't catch them. Index-based filtering blocks them instead.
  const highestUserMsgIndexRef = useRef(-1);

  // On WebSocket disconnect, leave session states untouched.
  // The backend agent tasks continue running independently — resetting
  // to "idle" was misleading. On reconnect, the recovery mechanism will
  // sync the real session states from the backend.
  const handleDisconnect = useCallback(() => {
    // No state mutation needed — connection status is already
    // tracked by `status` from useWebSocket (reconnecting/failed).
    void 0;
  }, []);

  // Handle a failed send — update message state and reset the optimistic
  // 'running' session4905 state to prevent the spinner from showing forever.
  const handleSendFailed = useCallback(
    (clientMsgId: string) => {
      updateSendState(clientMsgId, "failed");
      const activeId = urlSessionIdRef.current;
      if (activeId) {
        const currentState = sessionStatesRef.current.get(activeId);
        if (currentState === "running") {
          setSessionStateFor(activeId, "idle");
        }
      }
    },
    [updateSendState, setSessionStateFor],
  );

  const {
    status,
    connected,
    queueFull,
    sendMessage,
    confirmSend,
    sendAnswer,
    sendRecover,
    confirmRecover,
  } = useWebSocket({
    userId,
    onMessage: handleIncomingMessage,
    onDisconnect: handleDisconnect,
    onSendFailed: handleSendFailed,
    onRecoverTimeout: (sessionId: string) => {
      // Recover failed to yield data within the timeout window.
      // Reset to idle so the spinner doesn't show forever.
      if (sessionId === urlSessionIdRef.current) {
        setSessionStateFor(sessionId, "idle");
      }
    },
    onQueueFull: () => {
      // Queue overflow — show a non-blocking warning to the user
      logger.warn(
        "[WebSocket] Pending queue full. Messages will be dropped until connection restores.",
      );
    },
    token: authToken ?? undefined,
  });

  // Sync confirmSend to ref (so handleIncomingMessage can use it)
  useEffect(() => {
    confirmSendRef.current = confirmSend;
  }, [confirmSend]);

  // Sync sendRecover to ref (so handleIncomingMessage can use it)
  useEffect(() => {
    sendRecoverRef.current = sendRecover;
  }, [sendRecover]);

  // Sync confirmRecover to ref (so handleIncomingMessage can clear recover timers)
  useEffect(() => {
    confirmRecoverRef.current = confirmRecover;
  }, [confirmRecover]);

  // Auto-recover message history when WebSocket reconnects.
  // Waits for REST history fetch to complete before sending recover
  // so setMessages(msgs) doesn't wipe WebSocket-added messages.
  // Falls back to a 3s safety timeout in case REST fails silently.
  const didRecoverRef = useRef(false);
  const recoverTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (connected && urlSessionIdRef.current && !didRecoverRef.current) {
      const doRecover = () => {
        if (didRecoverRef.current) return;
        didRecoverRef.current = true;
        const lastIndex = loadLastKnownIndex(urlSessionIdRef.current!, userId);
        sendRecover(
          urlSessionIdRef.current!,
          messages.length > 0 ? lastIndex : 0,
        );
      };
      if (restLoadedRef.current) {
        doRecover();
      } else {
        // REST hasn't completed yet — poll until it does, with a safety timeout
        recoverTimeoutRef.current = setInterval(() => {
          if (restLoadedRef.current) {
            clearInterval(recoverTimeoutRef.current!);
            recoverTimeoutRef.current = null;
            doRecover();
          }
        }, 100);
        // Safety timeout: if REST never completes, recover anyway after 3s
        setTimeout(() => {
          if (recoverTimeoutRef.current) {
            clearInterval(recoverTimeoutRef.current);
            recoverTimeoutRef.current = null;
            doRecover();
          }
        }, 3000);
      }
    }
    if (!connected) {
      didRecoverRef.current = false;
      if (recoverTimeoutRef.current) {
        clearInterval(recoverTimeoutRef.current);
        recoverTimeoutRef.current = null;
      }
    }
  }, [connected, sendRecover]);

  // Heartbeat staleness detection: when the backend subscribe loop exits
  // after agent completion, heartbeats stop. If the completion signal was
  // lost (WS delivery failure), the frontend stays 'running' forever.
  // Detect this by checking if no heartbeat arrived for 60s while running.
  useEffect(() => {
    if (activeSessionStatus !== "running" || !urlSessionIdRef.current) return;

    const checkInterval = setInterval(() => {
      const sid = urlSessionIdRef.current;
      if (!sid) return;

      // Only trigger recovery when actually connected — sending
      // recover while disconnected is a no-op and resets the timer.
      if (!connected) return;

      const gap = Date.now() - lastHeartbeatRef.current;
      if (gap > 60_000) {
        // Subscribe loop exited silently — trigger recovery
        lastHeartbeatRef.current = Date.now(); // Reset to avoid repeated triggers
        sendRecover(sid, computeRecoverIndex(messages));
      }
    }, 10_000); // Check every 10s

    return () => clearInterval(checkInterval);
  }, [activeSessionStatus, messages, sendRecover, connected]);

  const handleResend = useCallback(
    (failedMessage: Message) => {
      const sessionId = urlSessionIdRef.current || failedMessage.session_id;
      if (!sessionId) return;

      const newClientMsgId = generateUUID();
      const files = (failedMessage.data as Array<{ filename?: string; stored_name?: string; size?: number }> | undefined) || [];

      setMessages((prev) =>
        prev.map((m) =>
          m.clientMsgId === failedMessage.clientMsgId
            ? { ...m, clientMsgId: newClientMsgId, sendState: "sending" as MessageSendState }
            : m,
        ),
      );
      sendStateMapRef.current.set(newClientMsgId, "sending");
      const resentMsg: Message = {
        ...failedMessage,
        clientMsgId: newClientMsgId,
        sendState: "sending",
      };
      pendingUserMsgsRef.current.set(sessionId, resentMsg);
      setSessionStateFor(sessionId, "running");

      sendMessage({
        message: failedMessage.content,
        session_id: sessionId,
        last_index: maxMsgIndexRef.current + 1,
        files: files.map((f) => ({
          stored_name: f.stored_name || f.filename || "",
          size: f.size ?? 0,
        })),
        client_msg_id: newClientMsgId,
        language: localStorage.getItem('i18nextLng') || 'zh',
      });
    },
    [sendMessage, setSessionStateFor],
  );


  const handleSend = useCallback(
    async (message: string, files?: File[], fileMeta?: Array<{stored_name: string; size: number}>) => {
      let sessionId = urlSessionIdRef.current;

      // Auto-create session if none exists
      if (!sessionId) {
        maxMsgIndexRef.current = -1;
        try {
          const headers: Record<string, string> = {};
          if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
          const resp = await fetch(`/api/users/${userId}/sessions`, {
            method: "POST",
            headers,
          });
          const data = await resp.json();
          sessionId = data.session_id;
          navigate("/chat/" + sessionId);
          await loadSessions();
        } catch (err) {
          const errorMsg = err instanceof Error ? err.message : String(err);
          logger.error("Session creation failed", errorMsg);
          setSessionCreateError(true);
          setTimeout(() => setSessionCreateError(false), 8000);
          return;
        }
      }

      // Use index = maxMsgIndex + 1 so it sorts AFTER all existing
      // messages (including the last assistant result) but won't collide
      // with backend-assigned indices during dedup. When the backend
      // echoes the user message, it will have its own proper index.
      const lastBackendIndex = maxMsgIndexRef.current;
      // Set threshold: messages with index >= this are "new turn".
      // When first such message arrives, clear old messages.
      clearThresholdRef.current = lastBackendIndex;
      replayStartedRef.current = false;
      const fileMetadata: Array<{ filename?: string; stored_name?: string; size: number }> | undefined =
        fileMeta?.map((f) => ({ stored_name: f.stored_name, size: f.size })) ||
        files?.map((f) => ({ filename: f.name, size: f.size }));
      const clientMsgId = generateUUID();
      const optimisticMsg: Message = {
        type: "user",
        content: message,
        index: lastBackendIndex + 1,
        data: fileMetadata,
        clientMsgId,
        sendState: "sending",
        session_id: sessionId ?? undefined,
      };
      // Track send state
      sendStateMapRef.current.set(clientMsgId, "sending");
      // Track pending message — survives session switches so it can be
      // restored when switching back, even if the backend hasn't received
      // the WebSocket message yet.
      if (sessionId) {
        pendingUserMsgsRef.current.set(sessionId, optimisticMsg);
        // Persist to localStorage so the pending message survives page refresh
        savePendingMessage(sessionId, userId, {
          content: message,
          clientMsgId,
          files: fileMetadata,
          timestamp: Date.now(),
        });
      }
      setMessages((prev) => {
        // If WebSocket echo already added this message (race: echo beat
        // the optimistic insert), update sendState from the source-of-truth
        // map instead of duplicating the user message.
        const existing = prev.find((m) => m.clientMsgId === clientMsgId);
        if (existing) {
          const state = sendStateMapRef.current.get(clientMsgId) ?? "sending";
          if (existing.sendState === state) return prev;
          return prev.map((m) =>
            m.clientMsgId === clientMsgId ? { ...m, sendState: state } : m,
          );
        }
        const updated = [...prev, optimisticMsg];
        // Update maxMsgIndexRef synchronously so follow-up sends use the correct anchor.
        if (optimisticMsg.index != null && optimisticMsg.index > maxMsgIndexRef.current) {
          maxMsgIndexRef.current = optimisticMsg.index;
        }
        return updated;
      });
      setSessionStateFor(sessionId!, "running");
      // Clear the suppress flag — a real session is now active
      suppressAutoActivateRef.current = false;

      // Send via WebSocket with send state tracking
      const currentLanguage = localStorage.getItem('i18nextLng') || 'zh';
      sendMessage({
        message,
        session_id: sessionId ?? undefined,
        last_index: lastBackendIndex + 1,
        files: fileMeta?.map((f) => ({ stored_name: f.stored_name, size: f.size }))
          || files?.map((f) => ({ stored_name: f.name, size: f.size })),
        client_msg_id: clientMsgId,
        language: currentLanguage,
      });

      // Monitor send outcome (timeout / disconnect)
      // Since sendMessage returns a clientMsgId, we track it here.
      // The actual resolution happens when backend echoes or timeout fires.
    },
    [messagesRef, sendMessage, authToken, userId, navigate],
  );

  const [newSessionKey, setNewSessionKey] = useState(0);

  const handleNewSession = useCallback(() => {
    // Reset tracking refs — no active session means input should be enabled
    clearThresholdRef.current = Number.MAX_SAFE_INTEGER;
    replayStartedRef.current = false;
    highestUserMsgIndexRef.current = -1;
    maxMsgIndexRef.current = -1;
    setStreamingTextState(useStreamingText.createInitialState());
    setSessionLoading(false);
    setMessages([]);
    // Prevent WebSocket messages from old sessions from re-activating
    suppressAutoActivateRef.current = true;
    // Force remount of / route's MainLayout for visible feedback
    setNewSessionKey(k => k + 1);
    navigate("/");
  }, [navigate]);


  const handleDeleteSession = useCallback(
    async (id: string) => {
      if (!confirm(t('sidebar.deleteSession'))) return;
      try {
        const headers: Record<string, string> = {};
        if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
        const resp = await fetch(`/api/users/${userId}/sessions/${id}`, {
          method: "DELETE",
          headers,
        });
        if (!resp.ok) {
          throw new Error(`Failed to delete session (HTTP ${resp.status})`);
        }
        // Clean up pending messages and last_known_index
        pendingUserMsgsRef.current.delete(id);
        clearLastKnownIndex(id, userId);
        // Small delay to ensure filesystem sync before reload
        await new Promise((r) => setTimeout(r, 200));
        // Refresh session list
        await loadSessions();
        // Clear if deleted the active session
        if (id === urlSessionId) {
          setMessages([]);
          // Clear this session's state from the map
          setSessionStatuss((prev) => {
            const next = new Map(prev);
            next.delete(id);
            return next;
          });
          // Reset replay tracking refs
          clearThresholdRef.current = Number.MAX_SAFE_INTEGER;
          replayStartedRef.current = false;
          highestUserMsgIndexRef.current = -1;
          navigate("/");
        }
      } catch (err) {
        logger.error("Failed to delete session", err);
        alert(err instanceof Error ? err.message : "Failed to delete session");
      }
    },
    [userId, authToken, urlSessionId, navigate, t],
  );

  const handleRenameSession = useCallback(
    async (sessionId: string, title: string) => {
      try {
        const headers: Record<string, string> = {};
        if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
        headers["Content-Type"] = "application/json";
        await fetch(
          `/api/users/${userId}/sessions/${sessionId}/title`,
          {
            method: "PATCH",
            headers,
            body: JSON.stringify({ title }),
          },
        );
        // Update local sessions state so sidebar reflects immediately
        setSessions((prev) =>
          prev.map((s) =>
            s.session_id === sessionId ? { ...s, title } : s,
          ),
        );
      } catch (err) {
        logger.error("Failed to rename session", err);
      }
    },
    [userId, authToken],
  );

  const handleLogout = useCallback(() => {
    localStorage.removeItem("authToken");
    localStorage.removeItem("userId");
    setAuthToken(null);
    setUserId("");
    setUserRole("user");
    setMessages([]);
    setSessions([]);
  }, []);

  const stopSession = useCallback(async () => {
    if (!urlSessionId) return;
    try {
      const headers: Record<string, string> = {};
      if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
      const resp = await fetch(
        `/api/users/${userId}/sessions/${urlSessionId}/cancel`,
        {
          method: "POST",
          headers,
        },
      );
      if (resp.ok) {
        setSessionStateFor(urlSessionId, "idle");
      }
    } catch (err) {
      logger.error("Failed to stop session", err);
    }
  }, [urlSessionId, userId, authToken]);

  // If no auth token, show login screen
  if (!authToken) {
    return (
      <LoginScreen
        onLogin={(uid) => {
          setUserId(uid);
          const token = localStorage.getItem("authToken");
          setAuthToken(token);
          if (token) {
            try {
              const payload = JSON.parse(atob(token.split(".")[1]));
              setUserRole(payload.role || "user");
            } catch {
              setUserRole("user");
            }
          }
        }}
      />
    );
  }

  return (
    <Routes>
      <Route
        path="/skills"
        element={
          <SkillsPage
            authToken={authToken}
            userId={userId}
            userRole={userRole}
            onBack={() => navigate("/")}
          />
        }
      />
      <Route
        path="/feedback"
        element={
          <FeedbackPage
            userId={userId}
            authToken={authToken}
            onBack={() => navigate("/")}
          />
        }
      />
      <Route
        path="/memory"
        element={
          <MemoryPanel
            userId={userId}
            authToken={authToken}
            onBack={() => navigate("/")}
          />
        }
      />
      <Route
        path="/mcp"
        element={
          <MCPPage
            userId={userId}
            authToken={authToken}
            onBack={() => navigate("/")}
          />
        }
      />
      <Route
        path="/evolution"
        element={
          <EvolutionPanel
            userId={userId}
            authToken={authToken}
            onBack={() => navigate("/")}
          />
        }
      />
      <Route
        path="/chat/:sessionId"
        element={
          <MainLayout
            status={status}
            queueFull={queueFull}
            userId={userId}
            sidebarOpen={sidebarOpen}
            setSidebarOpen={setSidebarOpen}
            sessions={sessions}
            activeSession={urlSessionId}
            onSelectSession={(id) => navigate("/chat/" + id)}
            onNewSession={handleNewSession}
            onDeleteSession={handleDeleteSession}
            onRenameSession={handleRenameSession}
            messages={messages}
            activeSessionStatus={activeSessionStatus}
            sendAnswer={sendAnswer}
            handleFileClick={handleFileClick}
            handleResend={handleResend}
            authToken={authToken}
            streamingText={streamingTextState.accumulatedText}
            inputBarRef={inputBarRef}
            handleSend={handleSend}
            stopSession={stopSession}
            filePanelOpen={filePanelOpen}
            setFilePanelOpen={setFilePanelOpen}
            fileRefreshKey={fileRefreshKey}
            setFileRefreshKey={setFileRefreshKey}
            handleLogout={handleLogout}
            navigate={navigate}
            sessionLoading={sessionLoading}
            sessionCreateError={sessionCreateError}
            setSessionCreateError={setSessionCreateError}
            userRole={userRole}
          />
        }
      />
      <Route
        path="/"
        element={
          <MainLayout
            key={newSessionKey}
            status={status}
            queueFull={queueFull}
            userId={userId}
            sidebarOpen={sidebarOpen}
            setSidebarOpen={setSidebarOpen}
            sessions={sessions}
            activeSession={urlSessionId}
            onSelectSession={(id) => navigate("/chat/" + id)}
            onNewSession={handleNewSession}
            onDeleteSession={handleDeleteSession}
            onRenameSession={handleRenameSession}
            messages={messages}
            activeSessionStatus={activeSessionStatus}
            sendAnswer={sendAnswer}
            handleFileClick={handleFileClick}
            handleResend={handleResend}
            authToken={authToken}
            streamingText={streamingTextState.accumulatedText}
            inputBarRef={inputBarRef}
            handleSend={handleSend}
            stopSession={stopSession}
            filePanelOpen={filePanelOpen}
            setFilePanelOpen={setFilePanelOpen}
            fileRefreshKey={fileRefreshKey}
            setFileRefreshKey={setFileRefreshKey}
            handleLogout={handleLogout}
            navigate={navigate}
            sessionLoading={sessionLoading}
            sessionCreateError={sessionCreateError}
            setSessionCreateError={setSessionCreateError}
            userRole={userRole}
          />
        }
      />
    </Routes>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/design-preview" element={<DesignPreviewPage />} />
      <Route path="/settings-preview" element={<SettingsPreviewPage />} />
      <Route path="/tech-preview" element={<TechPreviewPage />} />
      <Route path="/*" element={<MainApp />} />
    </Routes>
  );
}

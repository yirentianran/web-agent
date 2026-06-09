import {
  useState,
  useCallback,
  useEffect,
  useRef,
  type FormEvent,
} from "react";
import { Routes, Route, useNavigate, useMatch, Navigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { generateUUID } from "./lib/uuid";
import Sidebar from "./components/Sidebar";
import Header from "./components/Header";
import ThemeToggle from "./components/ThemeToggle";
import LanguageSwitcher from "./i18n/LanguageSwitcher";
import ChatArea from "./components/ChatArea";
import InputBar, { type InputBarHandle } from "./components/InputBar";
import SkillsPage from "./components/SkillsPage";
import SessionsPage from "./pages/SessionsPage";
import SessionFilePanel from "./components/SessionFilePanel";

import MCPPage from "./components/MCPPage";
import DashboardPage from "./components/DashboardPage";
import EvolutionPage from "./pages/EvolutionPage";
import UsersPage from "./pages/UsersPage";
import DesignPreviewPage from "./DesignPreviewPage";
import SettingsPreviewPage from "./SettingsPreviewPage";
import TechPreviewPage from "./TechPreviewPage";
import { useWebSocket } from "./hooks/useWebSocket";
import {
  useStreamingText,
  type StreamingTextState,
} from "./hooks/useStreamingText";
import type { Message, SessionItem, MessageSendState, ConnectionStatus, SessionStatus } from "./lib/types";
import { isUnconfirmed } from "./lib/types";
import {
  computeRecoverIndex,
  saveLastKnownIndex,
  loadLastKnownIndex,
  clearLastKnownIndex,
  savePendingMessage,
  clearPendingMessage,
  loadPendingMessage,
  resolveSessionState,
  resolveBufferState,
  TERMINAL_STATES,
} from "./lib/session-state";
import { apiFetch } from "./lib/api";
import { createLogger } from "./utils/logger";

const logger = createLogger("[App]");

// Valid session state transitions — used by setSessionStateFor to
// warn on unexpected transitions (soft check, no transition is blocked).
const VALID_TRANSITIONS: Record<string, SessionStatus[]> = {
  idle: ["running", "cancelled", "waiting_user"],
  running: ["completed", "error", "cancelled", "idle", "waiting_user"],
  completed: ["running"],
  error: ["running", "idle"],
  cancelled: ["running", "idle"],
  waiting_user: ["running", "completed", "error", "cancelled"],
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
  onLogin: (userId: string, role: string) => void;
}

const ACCOUNT_DISABLED = 'ACCOUNT_DISABLED';

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
        const serverDetail = detail.detail || '';
        if (resp.status === 403 && serverDetail === ACCOUNT_DISABLED) {
          throw new Error(ACCOUNT_DISABLED);
        }
        throw new Error(serverDetail || `HTTP ${resp.status}`);
      }

      const data = await resp.json();
      // Token is now in httpOnly cookie (set by backend). Store non-sensitive state.
      localStorage.setItem("userId", data.user_id);
      onLogin(trimmed, data.role || "user");
    } catch (err) {
      const msg = err instanceof Error ? err.message : '';
      if (msg === ACCOUNT_DISABLED) {
        setError(t('login.accountDisabled'));
      } else {
        setError(msg || t('login.error'));
      }
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
  handleSend: (message: string, fileMeta?: Array<{filename: string; size: number}>, sessionId?: string) => void;
  onEnsureSession: () => Promise<string | undefined>;
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
  onEnsureSession,
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

        onOpenEvolution={() => navigate("/evolution")}
        onOpenMCP={() => navigate("/mcp")}
        onOpenDashboard={() => navigate("/dashboard")}
        onOpenUsers={() => navigate("/users")}
        onOpenSessions={() => navigate("/sessions")}
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
            ref={inputBarRef}
            onSend={handleSend}
            onEnsureSession={onEnsureSession}
            onStop={stopSession}
            disabled={status !== "connected" || activeSessionStatus === "running"}
            isRunning={activeSessionStatus === "running" && status === "connected"}
            userId={userId}
            authToken={authToken || undefined}
            sessionId={activeSession || undefined}
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

  // ── Message helpers ──────────────────────────────────────────────
  /** Find the highest index in a message array. */
  const computeMaxIndex = (msgs: Message[]): number => {
    let max = 0;
    for (const m of msgs) {
      if (m.index != null && m.index > max) max = m.index;
    }
    return max;
  };

  /** Update a message matching clientMsgId with new index/sendState. */
  const updateByClientMsgId = (
    prev: Message[],
    clientMsgId: string,
    newIndex: number | undefined,
  ): Message[] =>
    prev.map((m) =>
      m.clientMsgId === clientMsgId
        ? { ...m, index: newIndex ?? m.index, sendState: undefined }
        : m,
    );

  // Update an optimistic user message when the backend echo arrives.
  // If the echo has a lower index than the optimistic message (can happen
  // when subscribe-loop indices don't match DB seq), only confirm send
  // state without downgrading the index.
  const applyEchoUpdate = (prev: Message[], msg: Message): Message[] =>
    prev.map((m) => {
      if (m.clientMsgId !== msg.clientMsgId) return m;
      if (msg.index != null && msg.index < (m.index ?? 0)) {
        return { ...m, sendState: undefined };
      }
      return { ...m, index: msg.index ?? m.index, sendState: undefined };
    });

  const [userId, setUserId] = useState<string>(() => {
    return localStorage.getItem("userId") || "";
  });
  const [authToken, setAuthToken] = useState<string | null>(null);
  const [userRole, setUserRole] = useState<string>("user");
  const [roleLoading, setRoleLoading] = useState<boolean>(!!localStorage.getItem("userId"));
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

  // Restore user role from httpOnly JWT cookie on page refresh.
  // The role is stored in the cookie's JWT, not in localStorage.
  useEffect(() => {
    if (!userId) return;
    fetch("/api/auth/me", { credentials: "same-origin" })
      .then((resp) => {
        if (resp.ok) return resp.json();
        throw new Error(`HTTP ${resp.status}`);
      })
      .then((data: { user_id?: string; role?: string }) => {
        if (data.role) setUserRole(data.role);
      })
      .catch(() => {
        // Cookie may be expired — ignore
      })
      .finally(() => setRoleLoading(false));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Sync InputBar key with active session, but skip auto-created sessions
  // so the InputBar state (files, input) survives the auto-create navigation.
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
  const sendFailStatusTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
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
    maxMsgIndexRef.current = computeMaxIndex(messages);
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

      // Load historical messages from backend (cookies sent automatically)
      fetch(`/api/users/${userId}/sessions/${urlSessionId}/history`, {
        credentials: "same-origin",
      })
        .then((resp) => {
          logger.debug(
            "[REST /history] status=%d ok=%s userId=%s sessionId=%s",
            resp.status, resp.ok, userId, urlSessionId,
          );
          if (resp.status === 401 || resp.status === 403 || resp.status === 404) {
            window.location.href = window.location.origin;
            throw new Error("Auth or permission denied");
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
          const msgs = (data as any[]).map((m: any) => {
            const normalized = { ...m, index: m.index ?? Number.MAX_SAFE_INTEGER, session_id: urlSessionId };
            // Backend sends client_msg_id; frontend dedup needs clientMsgId
            if (m.client_msg_id && !normalized.clientMsgId) {
              normalized.clientMsgId = m.client_msg_id;
            }
            return normalized;
          });
          // Immediately set session state from the live buffer state embedded
          // in every REST /history message. This prevents the spinner from
          // disappearing between the history load and the /status fetch.
          const liveState = msgs[0]?.session_state as SessionStatus | undefined;
          if (liveState === "error") {
            console.warn("[REST /history] liveState=error, msg[0].session_state=%s, buffer is in error state", msgs[0]?.session_state);
          }
          if (liveState) {
            setSessionStateFor(urlSessionId, liveState);
          }
          // Confirm user messages found in REST history (subscribe loop may skip
          // the user echo for new sessions due to last_seen threshold).
          for (const m of msgs) {
            if (m.type === "user" && m.clientMsgId) {
              clearSendState(m.clientMsgId);
              confirmSendRef.current(m.clientMsgId);
              // Clear pending message — backend has confirmed receipt
              if (m.session_id) {
                pendingUserMsgsRef.current.delete(m.session_id);
                clearPendingMessage(m.session_id, userId);
              }
            }
          }
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
            // Indices from confirmed messages only — optimistic (sending)
            // and failed messages have synthetic indices that can collide
            // with real seq values. Only historical messages
            // (no sendState) are truly confirmed.
            const confirmedIndices = new Set(
              sameSession.filter((m) => !m.sendState).map((m) => m.index),
            );
            const prevClientMsgIds = new Set(
              sameSession.filter((m) => m.clientMsgId).map((m) => m.clientMsgId),
            );
            const newMsgs = msgs.filter(
              (m: Message) =>
                !confirmedIndices.has(m.index) &&
                !(m.clientMsgId && prevClientMsgIds.has(m.clientMsgId)),
            );
            // Merge: sameSession may contain an optimistic user message
            // (sendState="sending") with a stale syntheticIndex. Re-index
            // it so it sorts after all confirmed messages.
            const merged = [...sameSession, ...newMsgs];
            const confirmedMax = computeMaxIndex(
              merged.filter((m) => !isUnconfirmed(m)),
            );
            const reindexed = merged.map((m) =>
              isUnconfirmed(m) && m.index <= confirmedMax
                ? { ...m, index: confirmedMax + 1 }
                : m,
            );
            if (newMsgs.length === 0) return reindexed;
            // Move unconfirmed messages (sending/failed) to the end
            const optimistic = reindexed.filter((m) => isUnconfirmed(m));
            const confirmed = reindexed.filter((m) => !isUnconfirmed(m));
            return [...confirmed, ...optimistic];
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
          const { state: resolvedFromHistory, shouldRecover: shouldRecoverFromHistory } =
            resolveSessionState(currentState, derivedState);
          // If the live buffer state from REST /history says "running",
          // it is authoritative — don't let history-derived state
          // (e.g. "completed" from a previous turn's result) downgrade it.
          const finalState: SessionStatus =
            liveState === "running" && resolvedFromHistory !== "running"
              ? "running"
              : (resolvedFromHistory as SessionStatus);
          if (shouldRecoverFromHistory) {
            sendRecover(
              urlSessionId!,
              msgs.length > 0
                ? computeRecoverIndex(msgs as unknown as Message[])
                : 0,
            );
            didRecoverRef.current = true;
          }
          setSessionStateFor(urlSessionId, finalState);
          fetch(`/api/users/${userId}/sessions/${urlSessionId}/status`, {
            credentials: "same-origin",
          })
            .then((resp) => {
              if (resp.status === 401 || resp.status === 403 || resp.status === 404) {
                window.location.href = window.location.origin;
                throw new Error("Permission denied");
              }
              return resp.json();
            })
            .then((status) => {
              if (urlSessionIdRef.current !== urlSessionId) return;
              const currentState2 = sessionStatesRef.current.get(urlSessionId) ?? "idle";
              console.log("[REST /status] currentState=%s status.state=%s bufferAge=%d", currentState2, status.state, status.buffer_age ?? 0);
              const { state: resolvedFromStatus, shouldRecover } =
                resolveBufferState(currentState2, status.state, status.buffer_age ?? 0);
              if (shouldRecover) {
                sendRecover(
                  urlSessionId!,
                  msgs.length > 0
                    ? computeRecoverIndex(msgs as unknown as Message[])
                    : 0,
                );
                didRecoverRef.current = true;
              }
              if (resolvedFromStatus !== currentState2) {
                setSessionStateFor(urlSessionId, resolvedFromStatus as SessionStatus);
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
      const resp = await fetch(`/api/users/${userId}/sessions`, { credentials: "same-origin" });
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
  // Also clear streaming text to avoid showing previous session's content.
  // Reset dedup refs: stale thresholds from prior session corrupt new dedup logic.
  useEffect(() => {
    lastHeartbeatRef.current = Date.now();
    setStreamingTextState(useStreamingText.createInitialState());
    replayStartedRef.current = false;
    clearThresholdRef.current = Number.MAX_SAFE_INTEGER;
    highestUserMsgIndexRef.current = -1;
    if (sendFailStatusTimerRef.current) {
      clearTimeout(sendFailStatusTimerRef.current);
      sendFailStatusTimerRef.current = null;
    }
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

  const clearSendState = useCallback(
    (clientMsgId: string | undefined) => {
      if (!clientMsgId) return;
      sendStateMapRef.current.delete(clientMsgId);
      setMessages((prev) =>
        prev.map((m) =>
          m.clientMsgId === clientMsgId ? { ...m, sendState: undefined } : m,
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

      // Handle auth failure — clear session and force re-login
      if (msg.type === "auth_error") {
        localStorage.removeItem("userId");
        setAuthToken(null);
        return;
      }

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
      // Only for the currently active session — prevent cross-session leak.
      const msgSessionId = msg.session_id || urlSessionIdRef.current;
      if (msgSessionId === urlSessionIdRef.current) {
        const newStreamingState = useStreamingText.processMessage(
          streamingTextStateRef.current,
          msg,
        );
        if (newStreamingState !== streamingTextStateRef.current) {
          streamingTextStateRef.current = newStreamingState;
          setStreamingTextState(newStreamingState);
        }
      }

      // Update last_known_index for persistence
      // Exclude stream_event — its index is computed (last_seen+i), not a real seq.
      if (
        msg.type !== "heartbeat" &&
        msg.type !== "system" &&
        msg.type !== "stream_event" &&
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
            clearSendState(pending.clientMsgId);
            confirmSendRef.current(pending.clientMsgId);
          }
        }
      }

      // Also confirm send if backend echoes a user message we're tracking (by clientMsgId on the incoming msg)
      if (msg.type === "user" && msg.clientMsgId) {
        clearSendState(msg.clientMsgId);
        confirmSendRef.current(msg.clientMsgId);
      }

      // Fallback: any agent message (assistant, tool_use, etc.) for a session
      // with a pending user message confirms the user message was received.
      // Needed because the subscribe loop may skip the user echo for new
      // sessions, and REST /history can race against WS message processing.
      if (
        msg.session_id &&
        msg.type !== "user" &&
        msg.type !== "heartbeat" &&
        msg.type !== "stream_event"
      ) {
        const pending = pendingUserMsgsRef.current.get(msg.session_id);
        if (pending?.clientMsgId) {
          clearSendState(pending.clientMsgId);
          confirmSendRef.current(pending.clientMsgId);
          pendingUserMsgsRef.current.delete(msg.session_id);
          clearPendingMessage(msg.session_id, userId);
        }
      }

      // stream_event indices are computed (last_seen+i) and can collide with real seq values
      const isInvisibleMessage =
        msg.type === "heartbeat" ||
        msg.type === "stream_event" ||
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
          if (newState === "error") {
            console.warn("[WS] session_state_changed:error replay=%s idx=%d currentState=%s", msg.replay, msg.index, sessionStatesRef.current.get(msg.session_id));
          }
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
              // Don't let a replayed terminal (e.g. "completed" from
              // a previous turn) override a live "running" state.
              if (currentState !== "running") {
                setSessionStateFor(msg.session_id, newState);
              }
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
            // When the state is "running" (set by REST /history) and a live
            // error arrives, it's likely a synthetic error from orphan
            // detection (server restart). Keep "running" so the spinner
            // shows until /status confirms the actual state.
            const currentState3 = sessionStatesRef.current.get(msg.session_id);
            if (newState === "error") {
              console.warn("[WS] live error received: currentState=%s msg=%s", currentState3, msg.message || msg.content);
              if (currentState3 !== "running") {
                setSessionStateFor(msg.session_id, newState);
              }
              // If running, orphan detection fired — keep "running"
            } else {
              setSessionStateFor(msg.session_id, newState);
            }
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
              next = applyEchoUpdate(prev, msg);
              dedupResult = "update:firstTurn-user-clientMsgIdDup";
            } else if (
              prev.some(
                (m) => m.type === "user" && m.content === msg.content,
              )
            ) {
              // Content match — update index of the first matching message
              next = prev.map((m) =>
                m.type === "user" && m.content === msg.content && m.sendState === "sending"
                  ? { ...m, index: msg.index ?? m.index, sendState: undefined }
                  : m,
              );
              dedupResult = "update:firstTurn-user-contentDup";
            } else {
              next = [...prev, msg];
              dedupResult = "append:firstTurn-user";
            }
          } else if (msg.clientMsgId && prev.some((m) => m.clientMsgId === msg.clientMsgId)) {
            next = updateByClientMsgId(prev, msg.clientMsgId, msg.index);
            dedupResult = "update:firstTurn-clientMsgIdDup";
          } else {
            next = [...prev, msg];
            dedupResult = "append:firstTurn-other";
          }
        } else if (msg.replay && prev.some((m) => m.index === msg.index)) {
          next = prev;
          dedupResult = "skip:replay-indexDup";
        } else if (msg.replay && msg.clientMsgId && prev.some((m) => m.clientMsgId === msg.clientMsgId)) {
          next = updateByClientMsgId(prev, msg.clientMsgId, msg.index);
          dedupResult = "update:replay-clientMsgIdDup";
        } else if (msg.type === "user" && !msg.replay) {
          if (
            msg.clientMsgId &&
            prev.some((m) => m.clientMsgId === msg.clientMsgId)
          ) {
            next = applyEchoUpdate(prev, msg);
            dedupResult = "update:user-clientMsgIdDup";
          } else if (
            !msg.clientMsgId &&
            prev.some((m) => m.type === "user" && m.content === msg.content)
          ) {
            // Content match — update index of the matching optimistic message
            next = prev.map((m) =>
              m.type === "user" && m.content === msg.content && m.sendState === "sending"
                ? { ...m, index: msg.index ?? m.index, sendState: undefined }
                : m,
            );
            dedupResult = "update:user-contentDup";
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
        } else if (msg.clientMsgId && prev.some((m) => m.clientMsgId === msg.clientMsgId)) {
          next = updateByClientMsgId(prev, msg.clientMsgId, msg.index);
          dedupResult = "update:fallthrough-clientMsgIdDup";
        } else {
          next = [...prev, msg];
          dedupResult = "append:fallthrough";
        }

        // Dedup file_result: when a new file_result arrives, remove old
        // file_result messages from the same session. The backend re-emits
        // cumulative file lists each turn, so old copies are stale.
        if (msg.type === "file_result" && msg.session_id) {
          next = next.filter(
            (m) => m.type !== "file_result" || m.index === msg.index,
          );
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
        maxMsgIndexRef.current = Math.max(
          maxMsgIndexRef.current,
          computeMaxIndex(withStates),
        );

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
        // Don't let a replayed result (e.g. from a previous turn)
        // override a live "running" state set by REST /history.
        const currentState = sessionStatesRef.current.get(msg.session_id);
        if (currentState !== "running") {
          setSessionStateFor(msg.session_id, "completed");
          loadSessions();
        }
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
          if (sendFailStatusTimerRef.current) clearTimeout(sendFailStatusTimerRef.current);
          sendFailStatusTimerRef.current = setTimeout(() => {
            if (urlSessionIdRef.current !== activeId) return;
            fetch(`/api/users/${userId}/sessions/${activeId}/status`, {
              credentials: "same-origin",
            })
              .then((r) => r.json())
              .then((status) => {
                if (urlSessionIdRef.current !== activeId) return;
                const currentState2 = sessionStatesRef.current.get(activeId) ?? "idle";
                const { state: resolved } = resolveBufferState(
                  currentState2, status.state, status.buffer_age ?? 0,
                );
                if (resolved !== currentState2) {
                  setSessionStateFor(activeId, resolved as SessionStatus);
                }
              })
              .catch(() => {});
          }, 5000);
        }
      }
    },
    [updateSendState, setSessionStateFor, userId],
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
    onConnectionFailed: (unconfirmedIds: string[]) => {
      logger.warn(
        "[WebSocket] Connection failed, marking %d unconfirmed messages as failed",
        unconfirmedIds.length,
      );
      const failedSet = new Set(unconfirmedIds);
      setMessages((prev) =>
        prev.map((m) =>
          m.clientMsgId && failedSet.has(m.clientMsgId)
            ? { ...m, sendState: "failed" as const }
            : m,
        ),
      );
      for (const id of unconfirmedIds) {
        sendStateMapRef.current.set(id, "failed");
      }
      // Reset session state for the active session if it was running
      const activeId = urlSessionIdRef.current;
      if (activeId && sessionStatesRef.current.get(activeId) === "running") {
        setSessionStateFor(activeId, "idle");
      }
    },
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
    onAuthFailed: () => {
      // JWT expired or invalid — clear session and redirect to login
      logger.warn("[WebSocket] Auth failed, redirecting to login");
      localStorage.removeItem("userId");
      setAuthToken(null);
    },
    token: undefined,
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
      const files = (failedMessage.data as Array<{ filename?: string; size?: number }> | undefined) || [];

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
          stored_name: f.filename || "",
          size: f.size ?? 0,
        })),
        client_msg_id: newClientMsgId,
        language: localStorage.getItem('i18nextLng') || 'zh',
      });
    },
    [sendMessage, setSessionStateFor],
  );

  const ensureSession = useCallback(async (): Promise<string | undefined> => {
    try {
      const resp = await apiFetch(`/api/users/${userId}/sessions`, {
        method: "POST",
      });
      if (!resp.ok) {
        const detail = await resp.json().then((d: { detail?: string }) => d.detail).catch(() => "");
        throw new Error(detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      const sessionId: string = data.session_id;
      if (!sessionId) {
        throw new Error("No session_id in response");
      }
      navigate("/chat/" + sessionId);
      await loadSessions();
      return sessionId;
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      logger.error("Session creation failed", errorMsg);
      return undefined;
    }
  }, [userId, navigate, loadSessions]);

  const handleSend = useCallback(
    async (message: string, fileMeta?: Array<{filename: string; size: number}>, paramSessionId?: string) => {
      const sessionId = paramSessionId || urlSessionIdRef.current;
      if (!sessionId) return;

      // Use index = maxMsgIndex + 1 so it sorts AFTER all existing
      // messages (including the last assistant result) but won't collide
      // with backend-assigned indices during dedup. When the backend
      // echoes the user message, it will have its own proper index.
      const lastBackendIndex = maxMsgIndexRef.current;
      const fileMetadata: Array<{ filename: string; size: number }> | undefined =
        fileMeta?.map((f) => ({ filename: f.filename, size: f.size }));
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
        const existing = prev.find((m) => m.clientMsgId === clientMsgId);
        if (existing) {
          const state = sendStateMapRef.current.get(clientMsgId) ?? "sending";
          if (existing.sendState === state) return prev;
          return prev.map((m) =>
            m.clientMsgId === clientMsgId ? { ...m, sendState: state } : m,
          );
        }
        // Recalculate true max from prev — maxMsgIndexRef can be stale.
        const trueMaxIdx = computeMaxIndex(prev);
        const adjustedIndex = Math.max(optimisticMsg.index ?? 0, trueMaxIdx + 1);
        const finalMsg = adjustedIndex !== optimisticMsg.index
          ? { ...optimisticMsg, index: adjustedIndex }
          : optimisticMsg;
        clearThresholdRef.current = trueMaxIdx;
        maxMsgIndexRef.current = adjustedIndex;
        replayStartedRef.current = false;
        return [...prev, finalMsg];
      });
      setSessionStateFor(sessionId, "running");
      suppressAutoActivateRef.current = false;

      // Send via WebSocket
      const currentLanguage = localStorage.getItem('i18nextLng') || 'zh';
      sendMessage({
        message,
        session_id: sessionId ?? undefined,
        last_index: lastBackendIndex + 1,
        files: fileMeta?.map((f) => ({ stored_name: f.filename, size: f.size })),
        client_msg_id: clientMsgId,
        language: currentLanguage,
      });
    },
    [messagesRef, sendMessage, userId, navigate],
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
    // Force remount of / route's MainLayout
    setNewSessionKey(k => k + 1);
    navigate("/");
  }, [navigate]);


  const handleDeleteSession = useCallback(
    async (id: string) => {
      if (!confirm(t('sidebar.deleteSession'))) return;
      try {
        const resp = await apiFetch(`/api/users/${userId}/sessions/${id}`, {
          method: "DELETE",
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
    [userId, urlSessionId, navigate, t],
  );

  const handleRenameSession = useCallback(
    async (sessionId: string, title: string) => {
      try {
        await apiFetch(
          `/api/users/${userId}/sessions/${sessionId}/title`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
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
    [userId],
  );

  const handleLogout = useCallback(() => {
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
      const resp = await apiFetch(
        `/api/users/${userId}/sessions/${urlSessionId}/cancel`,
        {
          method: "POST",
        },
      );
      if (resp.ok) {
        setSessionStateFor(urlSessionId, "cancelled");
      }
    } catch (err) {
      logger.error("Failed to stop session", err);
    }
  }, [urlSessionId, userId]);

  // If no auth token and no existing session, show login screen.
  // On initial load authToken is null, but cookies may exist from a previous login.
  if (!authToken && !localStorage.getItem("userId")) {
    return (
      <LoginScreen
        onLogin={(uid, role) => {
          setUserId(uid);
          setUserRole(role);
          setAuthToken("authenticated");
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
            onBack={() => navigate(-1)}
          />
        }
      />
      <Route
        path="/mcp"
        element={
          <MCPPage
            userId={userId}
            authToken={authToken}
            onBack={() => navigate(-1)}
          />
        }
      />
      <Route
        path="/dashboard"
        element={
          roleLoading ? null : userRole === "admin" ? (
            <DashboardPage />
          ) : (
            <Navigate to="/" replace />
          )
        }
      />
      <Route
        path="/evolution"
        element={
          roleLoading ? null : userRole === "admin" ? (
            <EvolutionPage />
          ) : (
            <Navigate to="/" replace />
          )
        }
      />
      <Route
        path="/users"
        element={
          roleLoading ? null : userRole === "admin" ? (
            <UsersPage />
          ) : (
            <Navigate to="/" replace />
          )
        }
      />
      <Route
        path="/sessions"
        element={
          roleLoading ? null : userRole === "admin" ? (
            <SessionsPage />
          ) : (
            <Navigate to="/" replace />
          )
        }
      />
      <Route
        path="/*"
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
            onEnsureSession={ensureSession}
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

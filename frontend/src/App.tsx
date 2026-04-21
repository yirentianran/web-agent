import {
  useState,
  useCallback,
  useEffect,
  useRef,
  type FormEvent,
} from "react";
import Sidebar from "./components/Sidebar";
import Header from "./components/Header";
import ChatArea from "./components/ChatArea";
import InputBar, { type InputBarHandle } from "./components/InputBar";
import SettingsPanel from "./components/SettingsPanel";
import FilesPanel from "./components/FilesPanel";
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
import type { Message, SessionItem, MessageSendState } from "./lib/types";
import {
  mergeSessionStates,
  computeRecoverIndex,
  saveLastKnownIndex,
  loadLastKnownIndex,
  clearLastKnownIndex,
} from "./lib/session-state";

const logger = {
  error: (message: string, err: unknown) => {
    const detail = err instanceof Error ? err.message : String(err);
    // In production, replace with a real logger (e.g., pino)
    // eslint-disable-next-line no-console
    console.error(`[App] ${message}: ${detail}`);
  },
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

// Check if we're on design preview page
function isDesignPreviewRoute(): boolean {
  return (
    window.location.pathname === "/design-preview" ||
    window.location.hash === "#/design-preview"
  );
}

// Check if we're on settings preview page
function isSettingsPreviewRoute(): boolean {
  return (
    window.location.pathname === "/settings-preview" ||
    window.location.hash === "#/settings-preview"
  );
}

// Check if we're on tech preview page
function isTechPreviewRoute(): boolean {
  return (
    window.location.pathname === "/tech-preview" ||
    window.location.hash === "#/tech-preview"
  );
}

interface LoginScreenProps {
  onLogin: (userId: string) => void;
}

function LoginScreen({ onLogin }: LoginScreenProps) {
  const [userId, setUserId] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = userId.trim();
    if (!trimmed) return;

    setLoading(true);
    setError("");

    try {
      const resp = await fetch("/api/auth/token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: trimmed }),
      });

      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
      }

      const data = await resp.json();
      localStorage.setItem("authToken", data.token);
      localStorage.setItem("userId", data.user_id);
      onLogin(trimmed);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  };

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
        <button
          className="login-button"
          type="submit"
          disabled={loading || !userId.trim()}
        >
          {loading ? "Logging in..." : "Login"}
        </button>
      </form>
    </div>
  );
}

// Main App component (internal)
function MainApp() {
  const [userId, setUserId] = useState<string>(() => {
    return localStorage.getItem("userId") || "default";
  });
  const [authToken, setAuthToken] = useState<string | null>(() => {
    return localStorage.getItem("authToken");
  });
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [activeSession, setActiveSession] = useState<string | null>(() => {
    return localStorage.getItem("activeSession");
  });
  const activeSessionRef = useRef<string | null>(null);
  // Keep ref in sync so handleIncomingMessage doesn't need activeSession as a dep
  useEffect(() => {
    activeSessionRef.current = activeSession;
  }, [activeSession]);
  // Persist activeSession to localStorage
  useEffect(() => {
    if (activeSession) {
      localStorage.setItem("activeSession", activeSession);
    } else {
      localStorage.removeItem("activeSession");
    }
  }, [activeSession]);
  const [sessionStates, setSessionStates] = useState<Map<string, string>>(
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

  // Per-session state setter — updates only the specified session.
  // Also syncs to sessionStatesRef to avoid stale closure bugs in
  // handleIncomingMessage when WebSocket messages arrive between
  // React scheduling a state update and applying it.
  const setSessionStateFor = useCallback((sessionId: string, state: string) => {
    // Sync to ref immediately — survives React render scheduling
    sessionStatesRef.current.set(sessionId, state);
    setSessionStates((prev) => {
      const next = new Map(prev);
      next.set(sessionId, state);
      return next;
    });
  }, []);

  // Get the current active session's state (for InputBar disabled check)
  const activeSessionState = activeSession
    ? (sessionStates.get(activeSession) ?? "idle")
    : "idle";
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [filesOpen, setFilesOpen] = useState(false);
  const [showFeedback, setShowFeedback] = useState(false);
  const [showEvolution, setShowEvolution] = useState(false);
  const [showMCP, setShowMCP] = useState(false);
  const [fileCount, setFileCount] = useState<number>(0);
  const inputBarRef = useRef<InputBarHandle>(null);
  // Index threshold: messages with index >= this are "new turn" messages.
  // Use MAX_SAFE_INTEGER so only replay messages trigger the first-turn path.
  // Live messages (index < MAX) fall through to normal append logic.
  const clearThresholdRef = useRef<number>(Number.MAX_SAFE_INTEGER);
  // Tracks whether replay has started for the current turn.
  // If replay sends messages, we don't clear (replay already handles ordering).
  const replayStartedRef = useRef(false);

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

  // Keep a ref to messages count for accurate last_index (Step 1 + 4)
  const messagesRef = useRef(0);
  const firstMessageRef = useRef<string | null>(null);
  useEffect(() => {
    messagesRef.current = messages.length;
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
    loadFileCount();
  }, [userId]);

  // Restore message history for the active session on mount (survives page refresh)
  useEffect(() => {
    if (activeSession) {
      // Load historical messages from backend
      const headers: Record<string, string> = {};
      if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
      fetch(`/api/users/${userId}/sessions/${activeSession}/history`, {
        headers,
      })
        .then((resp) => {
          if (resp.ok) return resp.json();
          return [];
        })
        .then((data) => {
          const msgs = (data as any[]).map((m: any) => ({
            ...m,
            // Use backend's absolute index; fallback to enumerate position
            index: m.index ?? -1,
            // Defensive: always ensure session_id for correct filtering
            session_id: activeSession,
          }));
          setMessages(msgs);
          // Derive sessionState from history
          let derivedState = "idle";
          for (let i = msgs.length - 1; i >= 0; i--) {
            const m = msgs[i];
            if (
              m.type === "system" &&
              m.subtype === "session_state_changed" &&
              m.state
            ) {
              derivedState = m.state;
              break;
            }
            if (m.type === "result") {
              derivedState = "completed";
              break;
            }
          }
          setSessionStateFor(activeSession, derivedState);
          // Fetch live buffer state — may differ from persisted DB state.
          // If buffer says running but is stale (>30s), don't trust it —
          // the agent likely exited and the completion signal was lost.
          fetch(`/api/users/${userId}/sessions/${activeSession}/status`, {
            headers,
          })
            .then((resp) => resp.json())
            .then((status) => {
              if (status.state === "running" && (status.buffer_age ?? 0) < 30) {
                setSessionStateFor(activeSession, "running");
              } else if (
                status.state === "running" &&
                (status.buffer_age ?? 0) >= 30
              ) {
                // Stale buffer — trigger recovery to get real state
                sendRecover(
                  activeSession!,
                  msgs.length > 0
                    ? computeRecoverIndex(msgs as unknown as Message[])
                    : 0,
                );
                didRecoverRef.current = true;
              }
            })
            .catch(() => {});
        })
        .catch(() => {
          setMessages([]);
          setSessionStateFor(activeSession, "idle");
        });
    }
  }, [userId, authToken]);

  const loadSessions = async () => {
    try {
      const headers: Record<string, string> = {};
      if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
      const resp = await fetch(`/api/users/${userId}/sessions`, { headers });
      if (resp.ok) {
        const data = await resp.json();
        setSessions(Array.isArray(data) ? data : []);
      }
    } catch {
      // Silently fail — sessions list is non-critical
    }
  };

  const loadFileCount = async () => {
    try {
      const resp = await fetch(`/api/users/${userId}/generated-files`);
      if (resp.ok) {
        const data = await resp.json();
        setFileCount(Array.isArray(data) ? data.length : 0);
      }
    } catch {
      setFileCount(0);
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
  }, [activeSession]);

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
      // Track heartbeat for staleness detection
      if (msg.type === "heartbeat") {
        lastHeartbeatRef.current = Date.now();
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
        const sid = msg.session_id || activeSessionRef.current;
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
      if (msg.type === "user" && !msg.replay && msg.session_id) {
        const pending = pendingUserMsgsRef.current.get(msg.session_id);
        const matchedByUuid =
          msg.clientMsgId && pending?.clientMsgId === msg.clientMsgId;
        const matchedByContent =
          !msg.clientMsgId && pending && pending.content === msg.content;
        if (matchedByUuid || matchedByContent) {
          pendingUserMsgsRef.current.delete(msg.session_id);
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
      if (msg.session_id && msg.session_id !== activeSessionRef.current) {
        if (msg.type === "system" && msg.subtype === "session_state_changed") {
          const newState = msg.state || msg.content || "completed";
          // Index-based filtering: block state changes from previous runs
          if (msg.index != null && msg.index < highestUserMsgIndexRef.current) {
            // Skip — this state change is older than the current run's user message
          } else if (msg.replay) {
            const currentState = sessionStatesRef.current.get(msg.session_id);
            // Allow error states through even during replay — they're more severe
            // But block completed/idle/cancelled from overwriting running
            if (
              currentState === "running" &&
              newState !== "running" &&
              newState !== "error"
            ) {
              // Skip — live state takes precedence
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
          const newState = msg.state || msg.content || "completed";
          // Index-based filtering: block state changes from previous runs
          if (msg.index != null && msg.index < highestUserMsgIndexRef.current) {
            // Skip — this state change is older than the current run's user message
          } else if (msg.replay) {
            const currentState = sessionStatesRef.current.get(msg.session_id);
            if (
              currentState === "running" &&
              newState !== "running" &&
              newState !== "error"
            ) {
              // Skip — live state takes precedence over replayed history
            } else {
              setSessionStateFor(msg.session_id, newState);
            }
          } else {
            setSessionStateFor(msg.session_id, newState);
          }
        }
        return;
      }

      // Use a functional update so we always work with the latest `prev`.
      // This avoids stale-closure bugs and ensures dedup runs on every message.
      setMessages((prev) => {
        const isFirstTurnMessage =
          !replayStartedRef.current &&
          (msg.replay || msg.index >= clearThresholdRef.current);

        if (isFirstTurnMessage) {
          replayStartedRef.current = true;
          // Append the incoming message to existing messages (which may
          // include recovered history). Dedup by index only.
          if (prev.some((m) => m.index === msg.index)) {
            return prev;
          }
          return [...prev, msg];
        }

        // Non-first message: append with dedup.
        // Replay dedup: skip if we already have this exact index
        if (msg.replay && prev.some((m) => m.index === msg.index)) {
          return prev;
        }
        // Live dedup for user messages: prefer UUID-based matching,
        // fallback to content match for backward compatibility with
        // messages that don't have clientMsgId.
        if (msg.type === "user" && !msg.replay) {
          if (
            msg.clientMsgId &&
            prev.some((m) => m.clientMsgId === msg.clientMsgId)
          ) {
            return prev;
          }
          // Fallback: content match for old messages without UUID
          if (
            !msg.clientMsgId &&
            prev.some((m) => m.type === "user" && m.content === msg.content)
          ) {
            return prev;
          }
        }
        // Live dedup for non-user messages: dedup by index to prevent
        // duplicates when messages arrive via both recovery and subscribe paths.
        if (!msg.replay && msg.type !== "user") {
          if (msg.index != null && prev.some((m) => m.index === msg.index)) {
            return prev;
          }
        }
        return [...prev, msg];
      });

      if (!activeSessionRef.current && msg.session_id) {
        setActiveSession(msg.session_id);
      }

      if (
        msg.type === "system" &&
        msg.subtype === "session_state_changed" &&
        msg.session_id
      ) {
        // Guard against old state changes from previous runs
        if (msg.index == null || msg.index >= highestUserMsgIndexRef.current) {
          setSessionStateFor(
            msg.session_id,
            msg.state || msg.content || "completed",
          );
        }
      }
      if (msg.type === "result" && msg.session_id) {
        // Guard against old result messages from previous runs
        if (msg.index == null || msg.index >= highestUserMsgIndexRef.current) {
          setSessionStateFor(msg.session_id, "completed");
        }
        // Auto-generate title from first message
        if (activeSessionRef.current && firstMessageRef.current) {
          fetch(
            `/api/users/${userId}/sessions/${activeSessionRef.current}/title`,
            {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ title: firstMessageRef.current }),
            },
          ).catch(() => {});
        }
        loadSessions();
      }
      // Refresh file count from server when new files are generated
      // (cannot blindly increment because the agent may overwrite existing files)
      if (msg.type === "file_result") {
        loadFileCount();
      }
    },
    [userId, updateSendState],
  );

  const {
    status,
    connected,
    sendMessage,
    confirmSend,
    sendAnswer,
    sendRecover,
  } = useWebSocket({
    userId,
    onMessage: handleIncomingMessage,
    token: authToken ?? undefined,
  });

  // Sync confirmSend to ref (so handleIncomingMessage can use it)
  useEffect(() => {
    confirmSendRef.current = confirmSend;
  }, [confirmSend]);

  // Auto-recover message history when WebSocket reconnects
  // Skip recovery on initial page load if REST already populated messages
  // Use persisted last_known_index for incremental recovery
  const didRecoverRef = useRef(false);
  useEffect(() => {
    if (connected && activeSessionRef.current && !didRecoverRef.current) {
      didRecoverRef.current = true;
      const lastIndex = loadLastKnownIndex(activeSessionRef.current, userId);
      // If we have cached messages, recover from last known index;
      // otherwise recover from 0 (first load)
      sendRecover(
        activeSessionRef.current,
        messages.length > 0 ? lastIndex : 0,
      );
    }
    // Reset recovery flag on disconnect so next reconnect can recover again
    if (!connected) {
      didRecoverRef.current = false;
    }
  }, [connected, sendRecover]);

  // Heartbeat staleness detection: when the backend subscribe loop exits
  // after agent completion, heartbeats stop. If the completion signal was
  // lost (WS delivery failure), the frontend stays 'running' forever.
  // Detect this by checking if no heartbeat arrived for 60s while running.
  useEffect(() => {
    if (activeSessionState !== "running" || !activeSessionRef.current) return;

    const checkInterval = setInterval(() => {
      const sid = activeSessionRef.current;
      if (!sid) return;

      const gap = Date.now() - lastHeartbeatRef.current;
      if (gap > 60_000) {
        // Subscribe loop exited silently — trigger recovery
        lastHeartbeatRef.current = Date.now(); // Reset to avoid repeated triggers
        sendRecover(sid, computeRecoverIndex(messages));
      }
    }, 10_000); // Check every 10s

    return () => clearInterval(checkInterval);
  }, [activeSessionState, messages, sendRecover]);

  // Refs to break circular dependency between handleIncomingMessage and useWebSocket
  const confirmSendRef = useRef<(clientMsgId: string) => void>(() => {});
  const sendStateMapRef = useRef<Map<string, MessageSendState>>(new Map());
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

  const handleSend = useCallback(
    async (message: string, files?: File[]) => {
      let sessionId = activeSessionRef.current;

      // Auto-create session if none exists
      if (!sessionId) {
        try {
          const headers: Record<string, string> = {};
          if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
          const resp = await fetch(`/api/users/${userId}/sessions`, {
            method: "POST",
            headers,
          });
          const data = await resp.json();
          sessionId = data.session_id;
          setActiveSession(sessionId);
          await loadSessions();
        } catch (err) {
          // Session creation failed — fall back to synthetic ID so UX isn't broken
          const errorMsg = err instanceof Error ? err.message : String(err);
          logger.error("Session creation failed, using synthetic ID", errorMsg);
          sessionId = `session_${userId}_${Date.now()}`;
          setActiveSession(sessionId);
          setSessionStateFor(sessionId, "error");
          setTimeout(() => setSessionStateFor(sessionId!, "idle"), 3000);
        }
      }

      // Add user message immediately for UI responsiveness.
      // Use index = lastBackendIndex - 1 so it sorts BEFORE any replay
      // messages (which start at lastBackendIndex) but won't collide
      // with them during dedup.
      const lastBackendIndex = messagesRef.current;
      // Set threshold: messages with index >= this are "new turn".
      // When first such message arrives, clear old messages.
      clearThresholdRef.current = lastBackendIndex;
      replayStartedRef.current = false;
      const fileMetadata = files?.map((f) => ({
        filename: f.name,
        size: f.size,
      }));
      const clientMsgId = crypto.randomUUID();
      const optimisticMsg: Message = {
        type: "user",
        content: message,
        index: lastBackendIndex - 1,
        data: fileMetadata,
        clientMsgId,
        sendState: "sending",
      };
      // Track send state
      sendStateMapRef.current.set(clientMsgId, "sending");
      // Track pending message — survives session switches so it can be
      // restored when switching back, even if the backend hasn't received
      // the WebSocket message yet.
      if (sessionId) {
        pendingUserMsgsRef.current.set(sessionId, optimisticMsg);
      }
      setMessages((prev) => [...prev, optimisticMsg]);
      setSessionStateFor(sessionId!, "running");

      // Send via WebSocket with send state tracking
      sendMessage({
        message,
        session_id: sessionId ?? undefined,
        last_index: lastBackendIndex,
        files: files?.map((f) => f.name),
        client_msg_id: clientMsgId,
      });

      // Monitor send outcome (timeout / disconnect)
      // Since sendMessage returns a clientMsgId, we track it here.
      // The actual resolution happens when backend echoes or timeout fires.
    },
    [messagesRef, sendMessage, authToken, userId],
  );

  const handleNewSession = useCallback(async () => {
    // Clean up old session's pending messages and last_known_index
    const oldSessionId = activeSessionRef.current;
    if (oldSessionId) {
      pendingUserMsgsRef.current.delete(oldSessionId);
    }
    setMessages([]);
    setActiveSession(null);
    // Reset tracking refs — no active session means input should be enabled
    clearThresholdRef.current = Number.MAX_SAFE_INTEGER;
    replayStartedRef.current = false;
    highestUserMsgIndexRef.current = -1;
    // Reset streaming text state for new session
    setStreamingTextState(useStreamingText.createInitialState());
  }, []);

  const handleSelectSession = useCallback(
    async (id: string) => {
      // Flush last_known_index for the old session before switching
      const oldSessionId = activeSessionRef.current;
      if (oldSessionId) {
        const oldMaxIndex = computeRecoverIndex(messages) - 1;
        if (oldMaxIndex >= 0) {
          saveLastKnownIndex(oldSessionId, oldMaxIndex, userId);
        }
        // Clean up pending messages for old session
        pendingUserMsgsRef.current.delete(oldSessionId);
      }

      setActiveSession(id);
      activeSessionRef.current = id; // Sync ref immediately — WS messages arriving
      // in the same tick must use the new session
      firstMessageRef.current = null;
      // Reset tracking refs
      clearThresholdRef.current = Number.MAX_SAFE_INTEGER;
      replayStartedRef.current = false;
      highestUserMsgIndexRef.current = -1;
      // Reset streaming text state for new session
      setStreamingTextState(useStreamingText.createInitialState());

      // Restore pending message for this session so the user sees their
      // message immediately, even if the backend hasn't received the
      // WebSocket message yet (rapid session switch scenario).
      const pending = pendingUserMsgsRef.current.get(id);
      if (pending) {
        setMessages([pending]);
      } else {
        setMessages([]);
      }

      // Load historical messages from backend
      try {
        const headers: Record<string, string> = {};
        if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
        const resp = await fetch(
          `/api/users/${userId}/sessions/${id}/history`,
          { headers },
        );
        if (resp.ok) {
          const data = await resp.json();
          const msgs = (data as any[]).map((m: any) => ({
            ...m,
            index: m.index ?? -1,
            session_id: id,
          }));

          // If backend hasn't confirmed the pending message yet (history
          // doesn't contain it), restore it after loading history so the
          // user's message isn't lost during the gap between ws.send() and
          // backend receipt.
          if (
            pending &&
            !msgs.some(
              (m: Message) =>
                m.type === "user" && m.content === pending.content,
            )
          ) {
            setMessages([pending, ...msgs]);
          } else {
            setMessages(msgs);
            // Backend confirmed — clear pending
            pendingUserMsgsRef.current.delete(id);
          }

          // Restore first user message for title
          const firstUser = msgs.find((m: Message) => m.type === "user");
          if (firstUser)
            firstMessageRef.current = firstUser.content.slice(0, 50);

          // Derive sessionState from the last session_state_changed message,
          // or fall back to 'idle' if none found.
          let derivedState = "idle";
          for (let i = msgs.length - 1; i >= 0; i--) {
            const m = msgs[i];
            if (
              m.type === "system" &&
              m.subtype === "session_state_changed" &&
              m.state
            ) {
              derivedState = m.state;
              break;
            }
            if (m.type === "result") {
              derivedState = "completed";
              break;
            }
          }

          // Fetch live buffer state BEFORE setting the derived state —
          // the buffer may have session_state_changed messages that haven't
          // been flushed to DB yet (e.g., agent just started). Merge the
          // two states, preferring the more "active" one.
          let bufferState: string | undefined;
          try {
            const statusResp = await fetch(
              `/api/users/${userId}/sessions/${id}/status`,
              { headers },
            );
            if (statusResp.ok) {
              const status = await statusResp.json();
              bufferState = status.state;
            }
          } catch {
            // Status endpoint unavailable — fall back to DB-derived state
          }

          const finalState = mergeSessionStates(bufferState, derivedState);
          setSessionStateFor(id, finalState);

          // After loading history, recover to catch up any live messages
          // from an active agent session. Use the max message index so
          // we don't miss or duplicate messages.
          sendRecover(id, computeRecoverIndex(msgs));
          didRecoverRef.current = true; // Prevent auto-recovery from sending duplicate recover

          // Update last_known_index from loaded history
          if (msgs.length > 0) {
            let maxIdx = msgs[0].index;
            for (let j = 1; j < msgs.length; j++) {
              if (msgs[j].index > maxIdx) maxIdx = msgs[j].index;
            }
            if (maxIdx >= 0) saveLastKnownIndex(id, maxIdx, userId);
          }
        } else {
          // History fetch failed — restore pending if available
          if (pending) {
            setMessages([pending]);
          } else {
            setMessages([]);
          }
          setSessionStateFor(id, "idle");
        }
      } catch {
        // History fetch failed — restore pending if available
        if (pending) {
          setMessages([pending]);
        } else {
          setMessages([]);
        }
        setSessionStateFor(id, "idle");
      }
    },
    [userId, authToken, messages, setSessionStateFor, sendRecover],
  );

  const handleDeleteSession = useCallback(
    async (id: string) => {
      if (!confirm("Delete this session?")) return;
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
        if (id === activeSession) {
          setMessages([]);
          setActiveSession(null);
          // Clear this session's state from the map
          setSessionStates((prev) => {
            const next = new Map(prev);
            next.delete(id);
            return next;
          });
          // Reset replay tracking refs
          clearThresholdRef.current = Number.MAX_SAFE_INTEGER;
          replayStartedRef.current = false;
          highestUserMsgIndexRef.current = -1;
        }
      } catch (err) {
        logger.error("Failed to delete session", err);
        alert(err instanceof Error ? err.message : "Failed to delete session");
      }
    },
    [userId, authToken, activeSession],
  );

  const handleLogout = useCallback(() => {
    localStorage.removeItem("authToken");
    localStorage.removeItem("userId");
    setAuthToken(null);
    setUserId("");
    setMessages([]);
    setActiveSession(null);
    setSessions([]);
  }, []);

  const stopSession = useCallback(async () => {
    if (!activeSession) return;
    try {
      const headers: Record<string, string> = {};
      if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
      const resp = await fetch(
        `/api/users/${userId}/sessions/${activeSession}/cancel`,
        {
          method: "POST",
          headers,
        },
      );
      if (resp.ok) {
        setSessionStateFor(activeSession, "idle");
      }
    } catch (err) {
      console.error("Failed to stop session", err);
    }
  }, [activeSession, userId, authToken]);

  // If no auth token, show login screen
  if (!authToken) {
    return (
      <LoginScreen
        onLogin={(uid) => {
          setUserId(uid);
          setAuthToken(localStorage.getItem("authToken"));
        }}
      />
    );
  }

  // Feedback management page
  if (showFeedback) {
    return (
      <FeedbackPage
        userId={userId}
        authToken={authToken}
        onBack={() => setShowFeedback(false)}
      />
    );
  }

  // Skill evolution review page
  if (showEvolution) {
    return (
      <EvolutionPanel
        userId={userId}
        authToken={authToken}
        onBack={() => setShowEvolution(false)}
      />
    );
  }

  // MCP servers management page
  if (showMCP) {
    return (
      <MCPPage
        userId={userId}
        authToken={authToken}
        onBack={() => setShowMCP(false)}
      />
    );
  }

  return (
    <div className="app">
      {/* Reconnection failure banner */}
      {status === "failed" && (
        <div className="connection-banner connection-banner--failed">
          <span>Connection lost after multiple attempts.</span>
          <button onClick={() => window.location.reload()}>Refresh Page</button>
        </div>
      )}
      {/* Reconnecting indicator */}
      {status === "reconnecting" && (
        <div className="connection-banner connection-banner--reconnecting">
          <span>Reconnecting...</span>
        </div>
      )}
      {/* Header */}
      <Header
        connectionStatus={status}
        userId={userId}
        onOpenSettings={() => setSettingsOpen(true)}
        onOpenFeedback={() => setShowFeedback(true)}
        onOpenEvolution={() => setShowEvolution(true)}
        onOpenMCP={() => setShowMCP(true)}
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
            authToken={authToken}
            streamingText={streamingTextState.accumulatedText}
          />
          <InputBar
            key={activeSession}
            ref={inputBarRef}
            onSend={handleSend}
            onStop={stopSession}
            disabled={
              status !== "connected" || activeSessionState === "running"
            }
            userId={userId}
          />
        </main>
      </div>

      {/* Settings Overlay */}
      {settingsOpen && (
        <SettingsPanel
          authToken={authToken ?? ""}
          userId={userId}
          onClose={() => setSettingsOpen(false)}
        />
      )}
      {filesOpen && (
        <FilesPanel userId={userId} onClose={() => setFilesOpen(false)} />
      )}
    </div>
  );
}

export default function App() {
  // Design Preview Route - check before any hooks
  if (isDesignPreviewRoute()) {
    return <DesignPreviewPage />;
  }

  // Settings Preview Route - check before any hooks
  if (isSettingsPreviewRoute()) {
    return <SettingsPreviewPage />;
  }

  // Tech Preview Route - check before any hooks
  if (isTechPreviewRoute()) {
    return <TechPreviewPage />;
  }

  return <MainApp />;
}

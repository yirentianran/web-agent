import { useEffect, useRef, useState, useCallback } from "react";
import type { Message } from "../lib/types";
import { generateUUID } from "../lib/uuid";
import { createLogger } from "../utils/logger";

const logger = createLogger("[WebSocket]");

/** Outgoing WebSocket message shape — sent from frontend to backend. */
export interface WSOutgoingMessage {
  type?: "chat" | "answer" | "recover";
  message?: string;
  session_id?: string;
  last_index?: number;
  files?: (string | { stored_name: string; size: number })[];
  answers?: Record<string, string>;
  client_msg_id?: string;
  language?: string;
}

/** Connection status enum — replaces the simple `connected` boolean. */
export type ConnectionStatus =
  | "connected"
  | "connecting"
  | "reconnecting"
  | "failed";

/** Pending send tracked by client_msg_id. */
interface PendingSend {
  timer: ReturnType<typeof setTimeout>;
  reject: (reason: string) => void;
}

const PENDING_QUEUE_MAX = 100;
const PRIORITY_QUEUE_MAX = 10; // Separate queue for answers (high priority)
const SEND_TIMEOUT_MS = 300_000; // 5 minutes
const RECOVER_TIMEOUT_MS = 60_000  // must be >= frontend heartbeat staleness threshold;

interface UseWebSocketOptions {
  userId: string;
  onMessage: (msg: Message) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
  onQueueFull?: () => void;
  onSendFailed?: (clientMsgId: string) => void;
  onRecoverTimeout?: (sessionId: string) => void;
  onAuthFailed?: () => void; // Called when WebSocket is rejected due to invalid/expired token
  token?: string;
}

export function useWebSocket({
  userId,
  onMessage,
  onConnect,
  onDisconnect,
  onQueueFull,
  onSendFailed,
  onRecoverTimeout,
  onAuthFailed,
  token,
}: UseWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [queueFull, setQueueFull] = useState(false);
  const reconnectAttempts = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const maxAttempts = 5;

  // Refs for callbacks — always uses latest values without re-creating the connect function.
  const onMessageRef = useRef(onMessage);
  const onConnectRef = useRef(onConnect);
  const onDisconnectRef = useRef(onDisconnect);
  const onQueueFullRef = useRef(onQueueFull);
  const onSendFailedRef = useRef(onSendFailed);
  const onRecoverTimeoutRef = useRef(onRecoverTimeout);
  const onAuthFailedRef = useRef(onAuthFailed);
  const tokenRef = useRef(token);
  const userIdRef = useRef(userId);
  const flushPendingRef = useRef<() => void>(() => {});

  // Track active recover calls — keyed by sessionId, value is the timeout timer
  const recoverTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  // Keep refs in sync on every render
  useEffect(() => {
    onMessageRef.current = onMessage;
    onConnectRef.current = onConnect;
    onDisconnectRef.current = onDisconnect;
    onQueueFullRef.current = onQueueFull;
    onSendFailedRef.current = onSendFailed;
    onRecoverTimeoutRef.current = onRecoverTimeout;
    onAuthFailedRef.current = onAuthFailed;
    tokenRef.current = token;
    userIdRef.current = userId;
  });

  // Queue for messages sent while WebSocket is not OPEN
  const pendingQueue = useRef<WSOutgoingMessage[]>([]);
  // Priority queue for answers (AskUserQuestion) — bypasses PENDING_QUEUE_MAX
  const priorityQueue = useRef<WSOutgoingMessage[]>([]);
  // Pending recover — stored when WebSocket is not yet OPEN, flushed on connect
  const pendingRecoverRef = useRef<{ sessionId: string; lastIndex: number } | null>(null);
  // Track in-flight sends for timeout / error handling
  const pendingSends = useRef<Map<string, PendingSend>>(new Map());

  const flushPending = useCallback(() => {
    const ws = wsRef.current;
    logger.debug("flushPending: wsRef=", ws?.readyState, "pendingQueue=", pendingQueue.current.length, "priorityQueue=", priorityQueue.current.length);
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const recover = pendingRecoverRef.current;
    if (recover) {
      pendingRecoverRef.current = null;
      const payload = JSON.stringify({
        type: "recover",
        session_id: recover.sessionId,
        last_index: recover.lastIndex,
        user_id: userIdRef.current,
      });
      logger.debug("flushPending: sending queued recover:", payload.slice(0, 120));
      ws.send(payload);
      const existing = recoverTimers.current.get(recover.sessionId);
      if (existing) clearTimeout(existing);
      const timer = setTimeout(() => {
        recoverTimers.current.delete(recover.sessionId);
        onRecoverTimeoutRef.current?.(recover.sessionId);
      }, RECOVER_TIMEOUT_MS);
      recoverTimers.current.set(recover.sessionId, timer);
    }
    while (priorityQueue.current.length > 0) {
      const msg = priorityQueue.current.shift()!;
      const payload = JSON.stringify({ type: "answer", ...msg, user_id: userIdRef.current });
      logger.debug("Sending priority:", payload.slice(0, 100));
      ws.send(payload);
    }
    while (pendingQueue.current.length > 0) {
      const msg = pendingQueue.current.shift()!;
      const payload = JSON.stringify({ type: "chat", ...msg, user_id: userIdRef.current });
      logger.debug("Sending pending:", payload.slice(0, 100));
      ws.send(payload);
    }
  }, []);

  // Keep flushPending ref in sync — connect reads from the ref, not the closure.
  flushPendingRef.current = flushPending;

  const failPendingSends = useCallback(() => {
    for (const [, ps] of pendingSends.current) {
      clearTimeout(ps.timer);
      ps.reject("disconnected");
    }
    pendingSends.current.clear();
  }, []);

  // Core connect function — stable reference, reads everything from refs.
  // Returns { ws, close } where close is a cleanup function that only affects
  // this specific WebSocket (critical for React StrictMode double-mount).
  // When no token and no userId, skips connection entirely (login/register page).
  const connect = useCallback(() => {
    if (!tokenRef.current && !userIdRef.current) {
      return { ws: null, cleanup: () => {} };
    }

    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }

    setStatus(reconnectAttempts.current === 0 ? "connecting" : "reconnecting");

    // Cookies (including httpOnly access_token) are sent automatically on the
    // WebSocket handshake. No need for a ?token= query parameter.
    const ws = new WebSocket(
      `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws`,
    );
    wsRef.current = ws;

    // Per-WebSocket intentional close flag — each WS has its own closure flag.
    // This is the key fix for StrictMode: two WebSocket instances exist
    // simultaneously, and each must independently know if IT was closed
    // intentionally, not whether some other WS was.
    let intentionalClose = false;
    let errorCloseTimer: ReturnType<typeof setTimeout> | null = null;

    ws.onopen = () => {
      reconnectAttempts.current = 0;
      setQueueFull(false);
      setStatus("connected");
      logger.debug("Connected, wsRef:", wsRef.current === ws, "readyState:", ws.readyState);
      onConnectRef.current?.();
      flushPendingRef.current();
    };

    ws.onmessage = (event) => {
      logger.debug("Received:", event.data.slice(0, 100));
      try {
        const data = JSON.parse(event.data);
        if (data.type === "auth_error") {
          intentionalClose = true;
        }
        onMessageRef.current(data as Message);
      } catch {
        // ignore parse errors
      }
    };

    ws.onclose = (event) => {
      if (errorCloseTimer) { clearTimeout(errorCloseTimer); errorCloseTimer = null; }
      logger.warn(
        'WebSocket closed — code:',
        event.code,
        'reason:',
        event.reason,
        'wasClean:',
        event.wasClean,
      );
      if (intentionalClose) return;
      if (event.code === 4001 || event.code === 4002) {
        localStorage.removeItem("userId");
        window.location.href = window.location.origin;
        return;
      }
      setStatus("reconnecting");
      onDisconnectRef.current?.();
      if (reconnectAttempts.current >= maxAttempts) {
        setStatus("failed");
        for (const [, ps] of pendingSends.current) {
          ps.reject("connection_failed");
        }
        pendingSends.current.clear();
        // Clear all recover timers — connection is gone
        for (const [, t] of recoverTimers.current) clearTimeout(t);
        recoverTimers.current.clear();
        // Clear any queued recover — the auto-recover effect will
        // send a fresh one on reconnect with correct last_index
        pendingRecoverRef.current = null;
        return;
      }
      const delay = Math.min(1000 * 2 ** reconnectAttempts.current, 10000);
      reconnectAttempts.current += 1;
      reconnectTimer.current = setTimeout(() => {
        connect();
      }, delay);
    };

    ws.onerror = () => {
      logger.error(
        'WebSocket onerror fired — connection attempt failed. ' +
        'Reconnect attempt: ' + reconnectAttempts.current,
      );
      // onclose normally fires after onerror, but for connection-refused
      // errors (server restart window) some browsers skip onclose entirely.
      // Fallback: if onclose doesn't fire within 500ms, trigger reconnect.
      if (errorCloseTimer) clearTimeout(errorCloseTimer);
      errorCloseTimer = setTimeout(() => {
        if (intentionalClose) return;
        logger.warn('WebSocket onclose did not fire after onerror — triggering reconnect');
        setStatus("reconnecting");
        onDisconnectRef.current?.();
        if (reconnectAttempts.current >= maxAttempts) {
          setStatus("failed");
          return;
        }
        const delay = Math.min(1000 * 2 ** reconnectAttempts.current, 10000);
        reconnectAttempts.current += 1;
        reconnectTimer.current = setTimeout(() => connect(), delay);
      }, 500);
    };

    const cleanup = () => {
      intentionalClose = true;
      if (ws.readyState === WebSocket.OPEN) {
        ws.close(1000, "cleanup");
      }
      // Only clear wsRef if this WebSocket is the current one.
      // During StrictMode double-mount, two WS instances exist briefly.
      // The second mount creates a new WS and sets wsRef.current to it.
      // When cleanup #1 runs, wsRef.current may already point to WS #2.
      if (wsRef.current === ws) {
        wsRef.current = null;
      }
      // Clear reconnect timer only if this cleanup owns it.
      // StrictMode may have scheduled reconnect for WS #1 before cleanup runs.
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      // Do NOT call failPendingSends() here - it clears the global pendingSends Map.
      // During StrictMode, cleanup #1 would clear sends that WS #2 might need.
      // Instead, let failPendingSends be called only on true disconnect (ws.onclose).
      // failPendingSends();  // REMOVED - causes StrictMode issues
    };

    return { ws, cleanup };
  }, [failPendingSends]);

  // The effect — each invocation captures its own WS and its own intentionalClose flag.
  // Skips connection when no credentials (login/register page).
  useEffect(() => {
    if (!token && !userId) return;
    const { cleanup } = connect();
    return cleanup;
  }, [connect, token, userId]);

  /**
   * Send a message. Returns a `{ clientMsgId }` that can be used to track
   * the send state.
   */
  const sendMessage = useCallback(
    (data: WSOutgoingMessage): { clientMsgId: string } => {
      const clientMsgId = data.client_msg_id || generateUUID();
      const enriched = { ...data, client_msg_id: clientMsgId };
      const ws = wsRef.current;
      logger.debug("sendMessage: ws=", ws?.readyState, "wsRef=", wsRef.current === ws, "userId=", userIdRef.current);

      const onReject = () => {
        onSendFailedRef.current?.(clientMsgId);
      };
      const timer = setTimeout(onReject, SEND_TIMEOUT_MS);
      pendingSends.current.set(clientMsgId, { timer, reject: onReject });

      if (ws?.readyState === WebSocket.OPEN) {
        const payload = JSON.stringify({ type: "chat", ...enriched, user_id: userIdRef.current });
        logger.debug("Sending direct:", payload.slice(0, 100));
        ws.send(payload);
      } else {
        if (pendingQueue.current.length < PENDING_QUEUE_MAX) {
          pendingQueue.current.push(enriched);
        } else {
          setQueueFull(true);
          onQueueFullRef.current?.();
        }
      }

      return { clientMsgId };
    },
    [],
  );

  /** Confirm that the backend received a sent message (called from App.tsx onMessage). */
  const confirmSend = useCallback((clientMsgId: string) => {
    const pending = pendingSends.current.get(clientMsgId);
    if (pending) {
      clearTimeout(pending.timer);
      pendingSends.current.delete(clientMsgId);
    }
  }, []);

  /**
   * Send an answer to AskUserQuestion. Uses a separate priority queue
   * so that answers are never silently dropped due to queue overflow.
   */
  const sendAnswer = useCallback(
    (sessionId: string, answers: Record<string, string>) => {
      const ws = wsRef.current;
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(
          JSON.stringify({
            type: "answer",
            session_id: sessionId,
            answers,
            user_id: userIdRef.current,
          }),
        );
      } else {
        if (priorityQueue.current.length < PRIORITY_QUEUE_MAX) {
          priorityQueue.current.push({
            type: "answer",
            session_id: sessionId,
            answers,
          });
        }
      }
    },
    [],
  );

  const sendRecover = useCallback(
    (sessionId: string, lastIndex: number) => {
      const ws = wsRef.current;
      logger.debug("sendRecover: ws=", ws?.readyState, "sessionId=", sessionId, "lastIndex=", lastIndex);
      if (ws?.readyState === WebSocket.OPEN) {
        const payload = JSON.stringify({
          type: "recover",
          session_id: sessionId,
          last_index: lastIndex,
          user_id: userIdRef.current,
          language: localStorage.getItem('i18nextLng') || 'zh',
        });
        logger.debug("sendRecover: sending directly:", payload.slice(0, 120));
        ws.send(payload);
        // Start a timeout — if no messages arrive for this session
        // within RECOVER_TIMEOUT_MS, the recover likely failed.
        const existing = recoverTimers.current.get(sessionId);
        if (existing) clearTimeout(existing);
        const timer = setTimeout(() => {
          recoverTimers.current.delete(sessionId);
          onRecoverTimeoutRef.current?.(sessionId);
        }, RECOVER_TIMEOUT_MS);
        recoverTimers.current.set(sessionId, timer);
      } else {
        // WebSocket not yet open — queue for flushPending to send on connect
        pendingRecoverRef.current = { sessionId, lastIndex };
      }
    },
    [],
  );

  /** Clear a pending recover timeout — called when messages arrive for a session. */
  const confirmRecover = useCallback((sessionId: string) => {
    const timer = recoverTimers.current.get(sessionId);
    if (timer) {
      clearTimeout(timer);
      recoverTimers.current.delete(sessionId);
    }
  }, []);

  return {
    status,
    connected: status === "connected",
    queueFull,
    sendMessage,
    confirmSend,
    failPendingSends,
    sendAnswer,
    sendRecover,
    confirmRecover,
    reconnect: connect,
    reconnectAttempts: reconnectAttempts.current,
    pendingQueueSize: pendingQueue.current.length,
  };
}

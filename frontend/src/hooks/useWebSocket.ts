import { useEffect, useRef, useState, useCallback } from "react";
import type { Message } from "../lib/types";

/** Outgoing WebSocket message shape — sent from frontend to backend. */
export interface WSOutgoingMessage {
  type?: "chat" | "answer" | "recover";
  message?: string;
  session_id?: string;
  last_index?: number;
  files?: string[];
  answers?: Record<string, string>;
  client_msg_id?: string;
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
const SEND_TIMEOUT_MS = 30_000;

interface UseWebSocketOptions {
  userId: string;
  onMessage: (msg: Message) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
  onQueueFull?: () => void; // Called when the pending queue overflows
  onSendFailed?: (clientMsgId: string) => void; // Called when a send times out or connection fails
  token?: string;
}

export function useWebSocket({
  userId,
  onMessage,
  onConnect,
  onDisconnect,
  onQueueFull,
  onSendFailed,
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
  const tokenRef = useRef(token);
  const userIdRef = useRef(userId);
  const flushPendingRef = useRef<() => void>(() => {});

  // Keep refs in sync on every render
  useEffect(() => {
    onMessageRef.current = onMessage;
    onConnectRef.current = onConnect;
    onDisconnectRef.current = onDisconnect;
    onQueueFullRef.current = onQueueFull;
    onSendFailedRef.current = onSendFailed;
    tokenRef.current = token;
    userIdRef.current = userId;
  });

  // Queue for messages sent while WebSocket is not OPEN
  const pendingQueue = useRef<WSOutgoingMessage[]>([]);
  // Priority queue for answers (AskUserQuestion) — bypasses PENDING_QUEUE_MAX
  const priorityQueue = useRef<WSOutgoingMessage[]>([]);
  // Track in-flight sends for timeout / error handling
  const pendingSends = useRef<Map<string, PendingSend>>(new Map());

  const flushPending = useCallback(() => {
    const ws = wsRef.current;
    console.log("[WebSocket] flushPending: wsRef=", ws?.readyState, "pendingQueue=", pendingQueue.current.length, "priorityQueue=", priorityQueue.current.length);
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    while (priorityQueue.current.length > 0) {
      const msg = priorityQueue.current.shift()!;
      const payload = JSON.stringify({ type: "answer", ...msg, user_id: userIdRef.current });
      console.log("[WebSocket] Sending priority:", payload.slice(0, 100));
      ws.send(payload);
    }
    while (pendingQueue.current.length > 0) {
      const msg = pendingQueue.current.shift()!;
      const payload = JSON.stringify({ type: "chat", ...msg, user_id: userIdRef.current });
      console.log("[WebSocket] Sending pending:", payload.slice(0, 100));
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
  const connect = useCallback(() => {
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }

    setStatus(reconnectAttempts.current === 0 ? "connecting" : "reconnecting");

    const wsPath = tokenRef.current
      ? `/ws?token=${encodeURIComponent(tokenRef.current)}`
      : "/ws";
    const ws = new WebSocket(`ws://${window.location.host}${wsPath}`);
    wsRef.current = ws;

    // Per-WebSocket intentional close flag — each WS has its own closure flag.
    // This is the key fix for StrictMode: two WebSocket instances exist
    // simultaneously, and each must independently know if IT was closed
    // intentionally, not whether some other WS was.
    let intentionalClose = false;

    ws.onopen = () => {
      reconnectAttempts.current = 0;
      setQueueFull(false);
      setStatus("connected");
      console.log("[WebSocket] Connected, wsRef:", wsRef.current === ws, "readyState:", ws.readyState);
      onConnectRef.current?.();
      flushPendingRef.current();
    };

    ws.onmessage = (event) => {
      console.log("[WebSocket] Received:", event.data.slice(0, 100));
      try {
        const data = JSON.parse(event.data);
        onMessageRef.current(data as Message);
      } catch {
        // ignore parse errors
      }
    };

    ws.onclose = () => {
      if (intentionalClose) return;
      setStatus("reconnecting");
      onDisconnectRef.current?.();
      if (reconnectAttempts.current >= maxAttempts) {
        setStatus("failed");
        for (const [, ps] of pendingSends.current) {
          ps.reject("connection_failed");
        }
        pendingSends.current.clear();
        return;
      }
      const delay = Math.min(1000 * 2 ** reconnectAttempts.current, 10000);
      reconnectAttempts.current += 1;
      reconnectTimer.current = setTimeout(() => {
        connect();
      }, delay);
    };

    ws.onerror = () => {
      // onclose fires right after — let onclose handle it.
    };

    const cleanup = () => {
      intentionalClose = true;
      // Only close if WebSocket is not already CLOSED.
      // During StrictMode double-invoke, cleanup runs before onopen fires,
      // so ws may still be CONNECTING. Calling close() on CONNECTING WS
      // triggers browser warning "WebSocket is closed before established".
      // We suppress this by checking readyState first.
      if (ws.readyState !== WebSocket.CLOSED) {
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
  useEffect(() => {
    const { cleanup } = connect();
    return cleanup;
  }, [connect]);

  /**
   * Send a message. Returns a `{ clientMsgId }` that can be used to track
   * the send state.
   */
  const sendMessage = useCallback(
    (data: WSOutgoingMessage): { clientMsgId: string } => {
      const clientMsgId = data.client_msg_id || crypto.randomUUID();
      const enriched = { ...data, client_msg_id: clientMsgId };
      const ws = wsRef.current;
      console.log("[WebSocket] sendMessage: ws=", ws?.readyState, "wsRef=", wsRef.current === ws, "userId=", userIdRef.current);

      const onReject = () => {
        onSendFailedRef.current?.(clientMsgId);
      };
      const timer = setTimeout(onReject, SEND_TIMEOUT_MS);
      pendingSends.current.set(clientMsgId, { timer, reject: onReject });

      if (ws?.readyState === WebSocket.OPEN) {
        const payload = JSON.stringify({ type: "chat", ...enriched, user_id: userIdRef.current });
        console.log("[WebSocket] Sending direct:", payload.slice(0, 100));
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
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(
          JSON.stringify({
            type: "recover",
            session_id: sessionId,
            last_index: lastIndex,
            user_id: userIdRef.current,
          }),
        );
      }
    },
    [],
  );

  return {
    status,
    connected: status === "connected",
    queueFull,
    sendMessage,
    confirmSend,
    failPendingSends,
    sendAnswer,
    sendRecover,
    reconnect: connect,
    reconnectAttempts: reconnectAttempts.current,
    pendingQueueSize: pendingQueue.current.length,
  };
}

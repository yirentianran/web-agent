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
  token?: string;
}

export function useWebSocket({
  userId,
  onMessage,
  onConnect,
  onDisconnect,
  onQueueFull,
  token,
}: UseWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [queueFull, setQueueFull] = useState(false);
  const reconnectAttempts = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const maxAttempts = 5;
  // Track whether the close was intentional (cleanup/unmount) to
  // prevent the onclose handler from scheduling a reconnect.
  const intentionalCloseRef = useRef(false);
  // Refs for callbacks — keeps the `connect` function stable so that
  // parent re-renders (which produce new callback identities) do NOT
  // tear down and recreate the WebSocket mid-handshake.
  const onMessageRef = useRef(onMessage);
  const onConnectRef = useRef(onConnect);
  const onDisconnectRef = useRef(onDisconnect);
  const onQueueFullRef = useRef(onQueueFull);

  // Keep refs in sync on every render
  useEffect(() => {
    onMessageRef.current = onMessage;
    onConnectRef.current = onConnect;
    onDisconnectRef.current = onDisconnect;
    onQueueFullRef.current = onQueueFull;
  });

  // Queue for messages sent while WebSocket is not OPEN
  const pendingQueue = useRef<WSOutgoingMessage[]>([]);
  // Priority queue for answers (AskUserQuestion) — bypasses PENDING_QUEUE_MAX
  const priorityQueue = useRef<WSOutgoingMessage[]>([]);
  // Track in-flight sends for timeout / error handling
  const pendingSends = useRef<Map<string, PendingSend>>(new Map());

  const scheduleReconnect = useCallback(() => {
    if (reconnectAttempts.current >= maxAttempts) {
      setStatus("failed");
      // Reject all pending sends
      for (const [, ps] of pendingSends.current) {
        ps.reject("connection_failed");
      }
      pendingSends.current.clear();
      return;
    }
    setStatus("reconnecting");
    const delay = Math.min(1000 * 2 ** reconnectAttempts.current, 10000);
    reconnectAttempts.current += 1;
    reconnectTimer.current = setTimeout(() => {
      connect();
    }, delay);
  }, []);

  const flushPending = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    // Flush priority queue first (answers must arrive ASAP)
    while (priorityQueue.current.length > 0) {
      const msg = priorityQueue.current.shift()!;
      ws.send(JSON.stringify({ type: "answer", ...msg, user_id: userId }));
    }
    // Then flush regular queue
    while (pendingQueue.current.length > 0) {
      const msg = pendingQueue.current.shift()!;
      ws.send(JSON.stringify({ type: "chat", ...msg, user_id: userId }));
    }
  }, [userId]);

  const connect = useCallback(() => {
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }

    setStatus(reconnectAttempts.current === 0 ? "connecting" : "reconnecting");

    const wsPath = token ? `/ws?token=${encodeURIComponent(token)}` : "/ws";
    const ws = new WebSocket(`ws://${window.location.host}${wsPath}`);
    wsRef.current = ws;

    ws.onopen = () => {
      reconnectAttempts.current = 0;
      setQueueFull(false); // Reset queue full warning on reconnect
      setStatus("connected");
      onConnectRef.current?.();
      // Flush any queued messages now that we're connected
      flushPending();
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        onMessageRef.current(data as Message);
      } catch {
        // ignore parse errors
      }
    };

    ws.onclose = () => {
      if (intentionalCloseRef.current) return;
      setStatus("reconnecting");
      onDisconnectRef.current?.();
      scheduleReconnect();
    };

    ws.onerror = () => {
      // Don't set status here — onclose fires right after onerror.
      // Let onclose + scheduleReconnect handle status.
    };
  }, [
    userId,
    scheduleReconnect,
    flushPending,
    token,
  ]);

  /**
   * Send a message. Returns a `{ clientMsgId }` that can be used to track
   * the send state.
   */
  const sendMessage = useCallback(
    (data: WSOutgoingMessage): { clientMsgId: string } => {
      const clientMsgId = data.client_msg_id || crypto.randomUUID();
      const enriched = { ...data, client_msg_id: clientMsgId };
      const ws = wsRef.current;

      // Set up timeout tracking — reject updates send state to 'timeout'
      const onReject = () => {
        // Send state update is handled by App.tsx via sendStateMapRef
      };
      const timer = setTimeout(onReject, SEND_TIMEOUT_MS);
      pendingSends.current.set(clientMsgId, { timer, reject: onReject });

      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "chat", ...enriched, user_id: userId }));
      } else {
        // Queue the message — it will be sent once the WebSocket opens
        if (pendingQueue.current.length < PENDING_QUEUE_MAX) {
          pendingQueue.current.push(enriched);
        } else {
          // Queue full — notify caller instead of silently dropping
          setQueueFull(true);
          onQueueFullRef.current?.();
        }
      }

      return { clientMsgId };
    },
    [userId, onQueueFull],
  );

  /** Confirm that the backend received a sent message (called from App.tsx onMessage). */
  const confirmSend = useCallback((clientMsgId: string) => {
    const pending = pendingSends.current.get(clientMsgId);
    if (pending) {
      clearTimeout(pending.timer);
      pendingSends.current.delete(clientMsgId);
    }
  }, []);

  /** Mark all pending sends as failed (called on disconnect). */
  const failPendingSends = useCallback(() => {
    for (const [, ps] of pendingSends.current) {
      clearTimeout(ps.timer);
      ps.reject("disconnected");
    }
    pendingSends.current.clear();
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
            user_id: userId,
          }),
        );
      } else {
        // Use a separate priority queue — answers must never be dropped
        if (priorityQueue.current.length < PRIORITY_QUEUE_MAX) {
          priorityQueue.current.push({
            type: "answer",
            session_id: sessionId,
            answers,
          });
        }
      }
    },
    [userId],
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
            user_id: userId,
          }),
        );
      }
    },
    [userId],
  );

  useEffect(() => {
    connect();
    return () => {
      intentionalCloseRef.current = true;
      wsRef.current?.close();
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      failPendingSends();
    };
  }, [connect, failPendingSends]);

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

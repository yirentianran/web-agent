import { useEffect, useRef, useState, useCallback } from 'react'
import type { Message } from '../lib/types'

/** Outgoing WebSocket message shape — sent from frontend to backend. */
export interface WSOutgoingMessage {
  type?: 'chat' | 'answer' | 'recover'
  message?: string
  session_id?: string
  last_index?: number
  files?: string[]
  answers?: Record<string, string>
  client_msg_id?: string
}

/** Connection status enum — replaces the simple `connected` boolean. */
export type ConnectionStatus = 'connected' | 'connecting' | 'reconnecting' | 'failed'

/** Pending send tracked by client_msg_id. */
interface PendingSend {
  timer: ReturnType<typeof setTimeout>
  reject: (reason: string) => void
}

const PENDING_QUEUE_MAX = 100
const SEND_TIMEOUT_MS = 30_000

interface UseWebSocketOptions {
  userId: string
  onMessage: (msg: Message) => void
  onConnect?: () => void
  onDisconnect?: () => void
  token?: string
}

export function useWebSocket({ userId, onMessage, onConnect, onDisconnect, token }: UseWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null)
  const [status, setStatus] = useState<ConnectionStatus>('connecting')
  const reconnectAttempts = useRef(0)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const maxAttempts = 5
  // Queue for messages sent while WebSocket is not OPEN
  const pendingQueue = useRef<WSOutgoingMessage[]>([])
  // Track in-flight sends for timeout / error handling
  const pendingSends = useRef<Map<string, PendingSend>>(new Map())

  const scheduleReconnect = useCallback(() => {
    if (reconnectAttempts.current >= maxAttempts) {
      setStatus('failed')
      // Reject all pending sends
      for (const [, ps] of pendingSends.current) {
        ps.reject('connection_failed')
      }
      pendingSends.current.clear()
      return
    }
    setStatus('reconnecting')
    const delay = Math.min(1000 * 2 ** reconnectAttempts.current, 10000)
    reconnectAttempts.current += 1
    reconnectTimer.current = setTimeout(() => {
      connect()
    }, delay)
  }, [])

  const flushPending = useCallback(() => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    while (pendingQueue.current.length > 0) {
      const msg = pendingQueue.current.shift()!
      ws.send(JSON.stringify({ type: 'chat', ...msg, user_id: userId }))
    }
  }, [userId])

  const connect = useCallback(() => {
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current)
      reconnectTimer.current = null
    }

    setStatus(reconnectAttempts.current === 0 ? 'connecting' : 'reconnecting')

    const wsPath = token ? `/ws?token=${encodeURIComponent(token)}` : '/ws'
    const ws = new WebSocket(`ws://${window.location.host}${wsPath}`)
    wsRef.current = ws

    ws.onopen = () => {
      reconnectAttempts.current = 0
      setStatus('connected')
      onConnect?.()
      // Flush any queued messages now that we're connected
      flushPending()
    }

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        onMessage(data as Message)
      } catch {
        // ignore parse errors
      }
    }

    ws.onclose = () => {
      setStatus('reconnecting')
      onDisconnect?.()
      scheduleReconnect()
    }

    ws.onerror = () => {
      // Don't set status here — onclose fires right after onerror.
      // Let onclose + scheduleReconnect handle status.
    }
  }, [userId, onMessage, onConnect, onDisconnect, scheduleReconnect, flushPending])

  /**
   * Send a message. Returns a `{ clientMsgId }` that can be used to track
   * the send state.
   */
  const sendMessage = useCallback((data: WSOutgoingMessage): { clientMsgId: string } => {
    const clientMsgId = data.client_msg_id || crypto.randomUUID()
    const enriched = { ...data, client_msg_id: clientMsgId }
    const ws = wsRef.current

    // Set up timeout tracking — reject updates send state to 'timeout'
    const onReject = () => {
      // Send state update is handled by App.tsx via sendStateMapRef
    }
    const timer = setTimeout(onReject, SEND_TIMEOUT_MS)
    pendingSends.current.set(clientMsgId, { timer, reject: onReject })

    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'chat', ...enriched, user_id: userId }))
    } else {
      // Queue the message — it will be sent once the WebSocket opens
      if (pendingQueue.current.length < PENDING_QUEUE_MAX) {
        pendingQueue.current.push(enriched)
      }
      // If queue is full, oldest messages are silently dropped (FIFO overflow)
    }

    return { clientMsgId }
  }, [userId, onMessage])

  /** Confirm that the backend received a sent message (called from App.tsx onMessage). */
  const confirmSend = useCallback((clientMsgId: string) => {
    const pending = pendingSends.current.get(clientMsgId)
    if (pending) {
      clearTimeout(pending.timer)
      pendingSends.current.delete(clientMsgId)
    }
  }, [])

  /** Mark all pending sends as failed (called on disconnect). */
  const failPendingSends = useCallback(() => {
    for (const [, ps] of pendingSends.current) {
      clearTimeout(ps.timer)
      ps.reject('disconnected')
    }
    pendingSends.current.clear()
  }, [])

  const sendAnswer = useCallback((sessionId: string, answers: Record<string, string>) => {
    const ws = wsRef.current
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: 'answer',
        session_id: sessionId,
        answers,
        user_id: userId,
      }))
    } else {
      // Queue the answer too
      pendingQueue.current.push({ type: 'answer', session_id: sessionId, answers })
    }
  }, [userId])

  const sendRecover = useCallback((sessionId: string, lastIndex: number) => {
    const ws = wsRef.current
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: 'recover',
        session_id: sessionId,
        last_index: lastIndex,
        user_id: userId,
      }))
    }
  }, [userId])

  useEffect(() => {
    connect()
    return () => {
      wsRef.current?.close()
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      failPendingSends()
    }
  }, [connect, failPendingSends])

  return {
    status,
    connected: status === 'connected',
    sendMessage,
    confirmSend,
    failPendingSends,
    sendAnswer,
    sendRecover,
    reconnect: connect,
    reconnectAttempts: reconnectAttempts.current,
    pendingQueueSize: pendingQueue.current.length,
  }
}

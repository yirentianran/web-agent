import { useEffect, useRef, useState, useCallback } from 'react'
import type { Message } from '../lib/types'

/** Outgoing WebSocket message shape — sent from frontend to backend. */
export interface WSOutgoingMessage {
  type?: 'chat' | 'answer'
  message?: string
  session_id?: string
  last_index?: number
  files?: string[]
  answers?: Record<string, string>
}

interface UseWebSocketOptions {
  userId: string
  onMessage: (msg: Message) => void
  onConnect?: () => void
  onDisconnect?: () => void
  token?: string
}

export function useWebSocket({ userId, onMessage, onConnect, onDisconnect, token }: UseWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null)
  const [connected, setConnected] = useState(false)
  const reconnectAttempts = useRef(0)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const maxAttempts = 5
  // Queue for messages sent while WebSocket is not OPEN
  const pendingQueue = useRef<WSOutgoingMessage[]>([])

  const scheduleReconnect = useCallback(() => {
    if (reconnectAttempts.current >= maxAttempts) return
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

    const wsPath = token ? `/ws?token=${encodeURIComponent(token)}` : '/ws'
    const ws = new WebSocket(`ws://${window.location.host}${wsPath}`)
    wsRef.current = ws

    ws.onopen = () => {
      reconnectAttempts.current = 0
      setConnected(true)
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
      setConnected(false)
      onDisconnect?.()
      scheduleReconnect()
    }

    ws.onerror = () => {
      setConnected(false)
    }
  }, [userId, onMessage, onConnect, onDisconnect, scheduleReconnect, flushPending])

  const sendMessage = useCallback((data: WSOutgoingMessage) => {
    const ws = wsRef.current
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'chat', ...data, user_id: userId }))
    } else {
      // Queue the message — it will be sent once the WebSocket opens
      pendingQueue.current.push(data)
    }
  }, [userId])

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

  useEffect(() => {
    connect()
    return () => {
      wsRef.current?.close()
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
    }
  }, [connect])

  return { connected, sendMessage, sendAnswer, reconnect: connect }
}

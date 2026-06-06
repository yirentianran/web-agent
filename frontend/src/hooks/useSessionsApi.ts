import { useState, useEffect, useCallback } from 'react'
import { fetchJson, postJson } from '../lib/api'

export interface SessionItem {
  session_id: string
  user_id: string
  title: string
  status: string
  message_count: number
  total_tokens: number
  created_at: number
  last_active_at: number
}

export interface SessionsListData {
  items: SessionItem[]
  total: number
  page: number
  page_size: number
}

interface AsyncState<T> {
  data: T | null
  loading: boolean
  error: string | null
}

export interface SessionsFilters {
  user_id: string
  status: string
  q: string
  from_date: string
  to_date: string
  sort: string
  order: string
}

export interface SessionsAggregate {
  overview: {
    total_sessions: number
    active_sessions: number
    total_users: number
    total_tokens: number
  }
  by_user: Array<{ user_id: string; session_count: number; message_count: number; total_tokens: number }>
  by_date: Array<{ date: string; session_count: number; message_count: number; total_tokens: number }>
}

const API_BASE = '/api/admin/sessions'
export const PAGE_SIZE = 20

export function useSessionsApi(filters: SessionsFilters, page: number) {
  const [refreshKey, setRefreshKey] = useState(0)

  const [list, setList] = useState<AsyncState<SessionsListData>>({
    data: null, loading: true, error: null,
  })

  const [aggregate, setAggregate] = useState<AsyncState<SessionsAggregate>>({
    data: null, loading: true, error: null,
  })

  const fetchList = useCallback(() => {
    setList((s) => ({ ...s, loading: true, error: null }))
    const params = new URLSearchParams()
    if (filters.user_id) params.set('user_id', filters.user_id)
    if (filters.status) params.set('status', filters.status)
    if (filters.q) params.set('q', filters.q)
    if (filters.from_date) params.set('from_date', filters.from_date)
    if (filters.to_date) params.set('to_date', filters.to_date)
    params.set('sort', filters.sort || 'created_at')
    params.set('order', filters.order || 'desc')
    params.set('page', String(page))
    params.set('page_size', String(PAGE_SIZE))

    fetchJson<{ items: SessionItem[]; total: number; page: number; page_size: number }>(
      `${API_BASE}?${params.toString()}`,
    )
      .then((data) => setList({ data, loading: false, error: null }))
      .catch((e: unknown) =>
        setList({ data: null, loading: false, error: e instanceof Error ? e.message : 'Unknown error' }),
      )
  }, [filters.user_id, filters.status, filters.q, filters.from_date, filters.to_date, filters.sort, filters.order, page])

  const fetchAggregate = useCallback(() => {
    setAggregate((s) => ({ ...s, loading: true, error: null }))
    const params = new URLSearchParams()
    if (filters.from_date) params.set('from_date', filters.from_date)
    if (filters.to_date) params.set('to_date', filters.to_date)

    fetchJson<SessionsAggregate>(`${API_BASE}/aggregate?${params.toString()}`)
      .then((data) => setAggregate({ data, loading: false, error: null }))
      .catch((e: unknown) =>
        setAggregate({ data: null, loading: false, error: e instanceof Error ? e.message : 'Unknown error' }),
      )
  }, [filters.from_date, filters.to_date])

  useEffect(() => { fetchList() }, [fetchList, refreshKey])
  useEffect(() => { fetchAggregate() }, [fetchAggregate, refreshKey])

  const refetch = useCallback(() => setRefreshKey((k) => k + 1), [])

  const cancelSession = useCallback(
    (sessionId: string, userId: string) =>
      postJson<{ status: string }>(`/api/users/${userId}/sessions/${sessionId}/cancel`),
    [],
  )

  const deleteSession = useCallback(
    (sessionId: string, userId: string) =>
      fetchJson<{ status: string }>(`/api/users/${userId}/sessions/${sessionId}`, { method: 'DELETE' }),
    [],
  )

  return { list, aggregate, cancelSession, deleteSession, refetch }
}

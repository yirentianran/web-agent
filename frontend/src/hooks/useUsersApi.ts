import { useState, useEffect, useCallback, useMemo } from 'react'

export interface UserItem {
  user_id: string
  role: 'admin' | 'user'
  status: 'active' | 'disabled'
  created_at: number
  last_active_at: number
  disabled_at: number | null
  disabled_by: string | null
  session_count: number
  total_tokens: number
}

export interface UsersListData {
  items: UserItem[]
  total: number
  page: number
  page_size: number
}

interface AsyncState<T> {
  data: T | null
  loading: boolean
  error: string | null
}

export interface UsersFilters {
  q: string
  role: string
  status: string
  sort: string
  order: string
}

const API_BASE = '/api/admin/users'

async function fetchJson<T>(url: string, token: string): Promise<T> {
  const headers: Record<string, string> = token
    ? { Authorization: `Bearer ${token}` }
    : {}
  const resp = await fetch(url, { headers })
  if (!resp.ok) {
    const detail = await resp
      .json()
      .then((b) => b.detail)
      .catch(() => resp.statusText)
    throw new Error(typeof detail === 'string' ? detail : resp.statusText)
  }
  return resp.json() as Promise<T>
}

async function postJson(url: string, token: string): Promise<UserItem> {
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  const resp = await fetch(url, { method: 'POST', headers })
  if (!resp.ok) {
    const detail = await resp
      .json()
      .then((b) => b.detail)
      .catch(() => resp.statusText)
    throw new Error(typeof detail === 'string' ? detail : resp.statusText)
  }
  const body = await resp.json()
  return body.data as UserItem
}

export function useUsersApi(filters: UsersFilters, page: number) {
  const authToken = useMemo(() => localStorage.getItem('authToken') || '', [])
  const [refreshKey, setRefreshKey] = useState(0)

  const [list, setList] = useState<AsyncState<UsersListData>>({
    data: null,
    loading: true,
    error: null,
  })

  const fetchList = useCallback(() => {
    setList((s) => ({ ...s, loading: true, error: null }))
    const params = new URLSearchParams()
    if (filters.q) params.set('q', filters.q)
    if (filters.role) params.set('role', filters.role)
    if (filters.status) params.set('status', filters.status)
    params.set('sort', filters.sort)
    params.set('order', filters.order)
    params.set('page', String(page))
    params.set('page_size', '20')

    fetchJson<{ success: boolean; data: UsersListData }>(
      `${API_BASE}?${params}`,
      authToken,
    )
      .then((res) => setList({ data: res.data, loading: false, error: null }))
      .catch((e: unknown) =>
        setList({
          data: null,
          loading: false,
          error: e instanceof Error ? e.message : 'Unknown error',
        }),
      )
  }, [authToken, filters.q, filters.role, filters.status, filters.sort, filters.order, page, refreshKey])

  useEffect(() => {
    fetchList()
  }, [fetchList])

  const disableUser = useCallback(
    (userId: string): Promise<UserItem> =>
      postJson(`${API_BASE}/${encodeURIComponent(userId)}/disable`, authToken),
    [authToken],
  )

  const enableUser = useCallback(
    (userId: string): Promise<UserItem> =>
      postJson(`${API_BASE}/${encodeURIComponent(userId)}/enable`, authToken),
    [authToken],
  )

  const promoteUser = useCallback(
    (userId: string): Promise<UserItem> =>
      postJson(`${API_BASE}/${encodeURIComponent(userId)}/promote`, authToken),
    [authToken],
  )

  const demoteUser = useCallback(
    (userId: string): Promise<UserItem> =>
      postJson(`${API_BASE}/${encodeURIComponent(userId)}/demote`, authToken),
    [authToken],
  )

  const refetch = useCallback(() => {
    setRefreshKey((k) => k + 1)
  }, [])

  return { list, disableUser, enableUser, promoteUser, demoteUser, refetch }
}

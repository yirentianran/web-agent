import { useState, useEffect, useCallback, useMemo, useRef } from 'react'

export interface OverviewData {
  active_users: number
  total_users: number
  new_users: number
  total_sessions: number
  total_input_tokens: number
  total_output_tokens: number
  total_cache_read_tokens: number
  total_cache_write_tokens: number
}

export interface DailyCount {
  date: string
  count: number
}

export interface DailyTokens {
  date: string
  input: number
  output: number
  cache_read: number
  cache_write: number
}

export interface TrendsData {
  daily_active_users: DailyCount[]
  daily_sessions: DailyCount[]
  daily_tokens: DailyTokens[]
}

export interface TopUser {
  user_id: string
  total_tokens: number
  session_count: number
}

export interface TopSkill {
  skill_name: string
  use_count: number
  unique_users: number
}

export interface RankingsData {
  top_users: TopUser[]
  top_skills: TopSkill[]
}

interface AsyncState<T> {
  data: T | null
  loading: boolean
  error: string | null
}

export interface DashboardApi {
  overview: AsyncState<OverviewData>
  trends: AsyncState<TrendsData>
  rankings: AsyncState<RankingsData>
  refetch: (from: string, to: string) => void
}

const API_BASE = '/api/admin/dashboard'

function formatDate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function todayStr(): string {
  return formatDate(new Date())
}

function daysAgoStr(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return formatDate(d)
}

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

export function useDashboardApi(
  initialFrom?: string,
  initialTo?: string,
): DashboardApi {
  const authToken = useMemo(() => localStorage.getItem('authToken') || '', [])
  const initialFromRef = useRef(initialFrom || daysAgoStr(30))
  const initialToRef = useRef(initialTo || todayStr())

  const [from, setFrom] = useState(initialFromRef.current)
  const [to, setTo] = useState(initialToRef.current)

  const [overview, setOverview] = useState<AsyncState<OverviewData>>({
    data: null,
    loading: true,
    error: null,
  })
  const [trends, setTrends] = useState<AsyncState<TrendsData>>({
    data: null,
    loading: true,
    error: null,
  })
  const [rankings, setRankings] = useState<AsyncState<RankingsData>>({
    data: null,
    loading: true,
    error: null,
  })

  const fetchAll = useCallback(
    (fromDate: string, toDate: string) => {
      setOverview((s) => ({ ...s, loading: true, error: null }))
      setTrends((s) => ({ ...s, loading: true, error: null }))
      setRankings((s) => ({ ...s, loading: true, error: null }))

      const params = `?from_date=${fromDate}&to_date=${toDate}`

      fetchJson<OverviewData>(`${API_BASE}/overview${params}`, authToken)
        .then((data) => setOverview({ data, loading: false, error: null }))
        .catch((e: unknown) =>
          setOverview({
            data: null,
            loading: false,
            error: e instanceof Error ? e.message : 'Unknown error',
          }),
        )

      fetchJson<TrendsData>(`${API_BASE}/trends${params}`, authToken)
        .then((data) => setTrends({ data, loading: false, error: null }))
        .catch((e: unknown) =>
          setTrends({
            data: null,
            loading: false,
            error: e instanceof Error ? e.message : 'Unknown error',
          }),
        )

      fetchJson<RankingsData>(`${API_BASE}/rankings${params}`, authToken)
        .then((data) => setRankings({ data, loading: false, error: null }))
        .catch((e: unknown) =>
          setRankings({
            data: null,
            loading: false,
            error: e instanceof Error ? e.message : 'Unknown error',
          }),
        )
    },
    [authToken],
  )

  useEffect(() => {
    fetchAll(from, to)
  }, [from, to, fetchAll])

  const refetch = useCallback((newFrom: string, newTo: string) => {
    setFrom(newFrom)
    setTo(newTo)
  }, [])

  return { overview, trends, rankings, refetch }
}

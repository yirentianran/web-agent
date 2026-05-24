import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { daysAgoStr, todayStr } from '../lib/dates'

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
  interval: string
  active_users: DailyCount[]
  sessions: DailyCount[]
  tokens: DailyTokens[]
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

function autoInterval(from: string, to: string): string {
  const days = Math.round(
    (new Date(to).getTime() - new Date(from).getTime()) / (1000 * 60 * 60 * 24),
  )
  if (days <= 0) return '5min'
  if (days <= 3) return 'hour'
  return 'day'
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
  const interval = useMemo(() => autoInterval(from, to), [from, to])

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
    (fromDate: string, toDate: string, interval: string) => {
      setOverview((s) => ({ ...s, loading: true, error: null }))
      setTrends((s) => ({ ...s, loading: true, error: null }))
      setRankings((s) => ({ ...s, loading: true, error: null }))

      const params = `?from_date=${fromDate}&to_date=${toDate}`
      const tStart = performance.now()

      const timedFetch = <T,>(label: string, url: string): Promise<T> => {
        const t0 = performance.now()
        return fetchJson<T>(url, authToken).then((data) => {
          console.log(`[Dashboard] ${label} done in ${(performance.now() - t0).toFixed(0)}ms`, data)
          return data
        })
      }

      Promise.all([
        timedFetch<OverviewData>("overview", `${API_BASE}/overview${params}`)
          .then((data) => setOverview({ data, loading: false, error: null }))
          .catch((e: unknown) =>
            setOverview({
              data: null,
              loading: false,
              error: e instanceof Error ? e.message : 'Unknown error',
            }),
          ),

        timedFetch<TrendsData>("trends", `${API_BASE}/trends${params}&interval=${interval}`)
          .then((data) => setTrends({ data, loading: false, error: null }))
          .catch((e: unknown) =>
            setTrends({
              data: null,
              loading: false,
              error: e instanceof Error ? e.message : 'Unknown error',
            }),
          ),

        timedFetch<RankingsData>("rankings", `${API_BASE}/rankings${params}`)
          .then((data) => setRankings({ data, loading: false, error: null }))
          .catch((e: unknown) =>
            setRankings({
              data: null,
              loading: false,
              error: e instanceof Error ? e.message : 'Unknown error',
            }),
          ),
      ]).then(() => {
        console.log(`[Dashboard] All 3 API calls settled in ${(performance.now() - tStart).toFixed(0)}ms`)
      })
    },
    [authToken],
  )

  useEffect(() => {
    fetchAll(from, to, interval)
  }, [from, to, interval, fetchAll])

  const refetch = useCallback((newFrom: string, newTo: string) => {
    setFrom(newFrom)
    setTo(newTo)
  }, [])

  return { overview, trends, rankings, refetch }
}

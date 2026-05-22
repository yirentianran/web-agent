import { useState, useEffect, useCallback, useMemo } from 'react'

export interface EvolutionItem {
  id: number
  skill_name: string
  from_version: string
  to_version: string
  source: string
  evolve_reason: string
  status: 'active' | 'under_review' | 'rolled_back' | 'proposed' | 'superseded'
  baseline_composite: number | null
  proposed_content: string | null
  created_at: number
  reviewed_at: number | null
  reviewed_by: string | null
  review_decision: string | null
  auto_rollback_at: number | null
  days_active?: number
  composite_score?: number
}

export interface EvolutionDetail extends EvolutionItem {
  snapshots: Snapshot[]
  signal_breakdown: SignalBreakdown | null
}

export interface Snapshot {
  snapshot_date: string
  usage_count: number
  unique_users: number
  avg_rating: number
  session_success_rate: number
  composite_score: number
}

export interface SignalBreakdown {
  rating: { current: number; baseline: number; delta_pct: number }
  usage: { current: number; baseline: number; delta_pct: number }
  session_success: { current: number; baseline: number; delta_pct: number }
}

export interface EvolutionDiff {
  from_version: string
  to_version: string
  diff: string
}

interface AsyncState<T> {
  data: T | null
  loading: boolean
  error: string | null
}

const API_BASE = '/api/admin/evolution'

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

export interface EvolutionApi {
  overview: AsyncState<{ items: EvolutionItem[]; total: number; page: number }>
  fetchDetail: (id: number) => Promise<EvolutionDetail>
  fetchDiff: (id: number) => Promise<EvolutionDiff>
  review: (id: number, decision: 'keep' | 'rollback' | 'discard') => Promise<void>
  refetch: () => void
}

export function useEvolutionApi(statusFilter?: string, page: number = 1) {
  const authToken = useMemo(() => localStorage.getItem('authToken') || '', [])
  const [refreshKey, setRefreshKey] = useState(0)

  const [overview, setOverview] = useState<
    AsyncState<{ items: EvolutionItem[]; total: number; page: number }>
  >({ data: null, loading: true, error: null })

  const fetchOverview = useCallback(() => {
    setOverview((s) => ({ ...s, loading: true, error: null }))
    const params = new URLSearchParams()
    if (statusFilter) params.set('status', statusFilter)
    params.set('page', String(page))
    params.set('page_size', '20')
    fetchJson<{ items: EvolutionItem[]; total: number; page: number }>(
      `${API_BASE}/overview?${params}`,
      authToken,
    )
      .then((data) => setOverview({ data, loading: false, error: null }))
      .catch((e: unknown) =>
        setOverview({
          data: null,
          loading: false,
          error: e instanceof Error ? e.message : 'Unknown error',
        }),
      )
  }, [authToken, statusFilter, page, refreshKey])

  useEffect(() => {
    fetchOverview()
  }, [fetchOverview])

  const fetchDetail = useCallback(
    (id: number): Promise<EvolutionDetail> =>
      fetchJson<EvolutionDetail>(`${API_BASE}/${id}`, authToken),
    [authToken],
  )

  const fetchDiff = useCallback(
    (id: number): Promise<EvolutionDiff> =>
      fetchJson<EvolutionDiff>(`${API_BASE}/${id}/diff`, authToken),
    [authToken],
  )

  const review = useCallback(
    async (id: number, decision: 'keep' | 'rollback' | 'discard') => {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      }
      if (authToken) headers['Authorization'] = `Bearer ${authToken}`
      const resp = await fetch(`${API_BASE}/${id}/review`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ decision }),
      })
      if (!resp.ok) {
        const detail = await resp
          .json()
          .then((b) => b.detail)
          .catch(() => resp.statusText)
        throw new Error(typeof detail === 'string' ? detail : resp.statusText)
      }
      setRefreshKey((k) => k + 1)
    },
    [authToken],
  )

  const refetch = useCallback(() => {
    setRefreshKey((k) => k + 1)
  }, [])

  return { overview, fetchDetail, fetchDiff, review, refetch }
}

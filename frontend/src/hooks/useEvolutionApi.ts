import { useState, useEffect, useCallback, useMemo } from 'react'
import { fetchJson, csrfHeaders } from '../lib/api'

export interface EvolutionItem {
  id: number
  skill_name: string
  from_version: string
  to_version: string
  source: string
  evolve_reason: string
  status: 'active' | 'under_review' | 'rolled_back' | 'proposed' | 'superseded'
  baseline_composite: number | null
  baseline_metrics: string | null
  proposed_content: string | null
  instinct_count: number
  composite_score: number | null
  days_active: number
  created_at: number
  reviewed_at: number | null
  reviewed_by: string | null
  review_decision: string | null
  auto_rollback_at: number | null
}

export interface EvolutionDetail extends EvolutionItem {
  instincts?: InstinctItem[]
}

export interface TrendPoint {
  date: string
  success_rate: number
  usage_count: number
}

export interface EvolutionSignals {
  success_rate: { current: number; baseline: number; delta_pct: number }
  usage_count: { current: number; baseline: number; delta_pct: number }
}

export interface EvolutionDiff {
  from_version: string
  to_version: string
  diff: string
}

export interface InstinctItem {
  id: number
  domain: 'tool_usage' | 'task_orchestration'
  normalized_trigger: string
  trigger: string
  action: string
  confidence: number
  source_count: number
  unique_user_count: number
  scope: 'active' | 'deprecated'
  created_at: number
}

export interface ObservationItem {
  id: number
  session_id: string
  session_title: string
  user_id: string
  event_type: string
  tool_name: string
  tool_input_summary: string
  tool_output_summary: string
  success: boolean | null
  error_message: string
  duration_ms: number
  created_at: number
  message_seq: number | null
}

export interface SessionMessage {
  type: string
  seq: number
  subtype?: string
  name?: string
  content?: string
  input?: Record<string, unknown>
  result_content?: string
  tool_use_id?: string
}

export interface EvolutionStats {
  today_events: number
  active_instincts: number
  pending_reviews: number
  week_auto_applied: number
  time_window: string
  funnel: {
    observations: number
    active_instincts: number
    active_evolutions: number
    proposed_evolutions: number
  }
}

interface AsyncState<T> {
  data: T | null
  loading: boolean
  error: string | null
}

const API_BASE = '/api/admin/evolution'

export interface EvolutionApi {
  overview: AsyncState<{ items: EvolutionItem[]; total: number; page: number }>
  stats: AsyncState<EvolutionStats | null>
  instincts: AsyncState<{ items: InstinctItem[]; total: number; page: number }>
  observations: AsyncState<{ items: ObservationItem[]; total: number; page: number }>
  fetchDetail: (id: number) => Promise<EvolutionDetail>
  fetchDiff: (id: number) => Promise<EvolutionDiff>
  fetchTrend: (id: number, days?: number) => Promise<TrendPoint[]>
  fetchSignals: (id: number) => Promise<EvolutionSignals>
  review: (id: number, decision: 'keep' | 'rollback' | 'discard') => Promise<void>
  fetchStats: (days?: number) => Promise<void>
  fetchInstincts: (params?: { domain?: string; scope?: string; page?: number }) => Promise<void>
  fetchObservations: (params?: { session_id?: string; event_type?: string; page?: number }) => Promise<void>
  extractResult: AsyncState<{ extracted: number; clusters: number; applied: number; proposed: number; skipped?: boolean } | null>
  extractNow: () => Promise<void>
  fetchSessionMessages: (sessionId: string, aroundSeq?: number, context?: number) => Promise<SessionMessage[]>
  refetch: () => void
}

export function useEvolutionApi(statusFilter?: string, page: number = 1) {
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
    )
      .then((data) => setOverview({ data, loading: false, error: null }))
      .catch((e: unknown) =>
        setOverview({
          data: null,
          loading: false,
          error: e instanceof Error ? e.message : 'Unknown error',
        }),
      )
  }, [statusFilter, page, refreshKey])

  useEffect(() => {
    fetchOverview()
  }, [fetchOverview])

  const fetchDetail = useCallback(
    (id: number): Promise<EvolutionDetail> =>
      fetchJson<EvolutionDetail>(`${API_BASE}/${id}`),
    [],
  )

  const fetchDiff = useCallback(
    (id: number): Promise<EvolutionDiff> =>
      fetchJson<EvolutionDiff>(`${API_BASE}/${id}/diff`),
    [],
  )

  const review = useCallback(
    async (id: number, decision: 'keep' | 'rollback' | 'discard') => {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        ...csrfHeaders(),
      }
      const resp = await fetch(`${API_BASE}/${id}/review`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ decision }),
        credentials: 'same-origin',
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
    [],
  )

  // --- NEW: stats state ---
  const [stats, setStats] = useState<AsyncState<EvolutionStats | null>>({
    data: null,
    loading: false,
    error: null,
  })

  const fetchStats = useCallback(async (days: number = 0) => {
    setStats((s) => ({ ...s, loading: true, error: null }))
    try {
      const qs = days > 0 ? `?days=${days}` : ''
      const data = await fetchJson<EvolutionStats>(
        `${API_BASE}/stats${qs}`,
      )
      setStats({ data, loading: false, error: null })
    } catch (e: unknown) {
      setStats({
        data: null,
        loading: false,
        error: e instanceof Error ? e.message : 'Unknown error',
      })
    }
  }, [])

  // --- NEW: instincts state ---
  const [instincts, setInstincts] = useState<
    AsyncState<{ items: InstinctItem[]; total: number; page: number }>
  >({ data: null, loading: false, error: null })

  const fetchInstincts = useCallback(
    async (params?: { domain?: string; scope?: string; page?: number }) => {
      setInstincts((s) => ({ ...s, loading: true, error: null }))
      try {
        const qs = new URLSearchParams()
        if (params?.domain) qs.set('domain', params.domain)
        if (params?.scope) qs.set('scope', params.scope)
        if (params?.page) qs.set('page', String(params.page))
        const data = await fetchJson<{ items: InstinctItem[]; total: number; page: number }>(
          `/api/admin/instincts?${qs.toString()}`,
        )
        setInstincts({ data, loading: false, error: null })
      } catch (e: unknown) {
        setInstincts({
          data: null,
          loading: false,
          error: e instanceof Error ? e.message : 'Unknown error',
        })
      }
    },
    [],
  )

  // --- NEW: observations state ---
  const [observations, setObservations] = useState<
    AsyncState<{ items: ObservationItem[]; total: number; page: number }>
  >({ data: null, loading: false, error: null })

  const fetchObservations = useCallback(
    async (params?: { session_id?: string; event_type?: string; page?: number }) => {
      setObservations((s) => ({ ...s, loading: true, error: null }))
      try {
        const qs = new URLSearchParams()
        if (params?.session_id) qs.set('session_id', params.session_id)
        if (params?.event_type) qs.set('event_type', params.event_type)
        if (params?.page) qs.set('page', String(params.page))
        const data = await fetchJson<{ items: ObservationItem[]; total: number; page: number }>(
          `/api/admin/observations?${qs.toString()}`,
        )
        setObservations({ data, loading: false, error: null })
      } catch (e: unknown) {
        setObservations({
          data: null,
          loading: false,
          error: e instanceof Error ? e.message : 'Unknown error',
        })
      }
    },
    [],
  )

  const fetchSessionMessages = useCallback(
    (sessionId: string, aroundSeq?: number, context?: number): Promise<SessionMessage[]> => {
      const params = new URLSearchParams()
      if (aroundSeq !== undefined && aroundSeq !== null) {
        params.set('around_seq', String(aroundSeq))
        params.set('context', String(context ?? 5))
      }
      const qs = params.toString()
      const url = `/api/admin/sessions/${sessionId}/messages${qs ? '?' + qs : ''}`
      return fetchJson<{ items: SessionMessage[] }>(url).then((data) => data.items)
    },
    [],
  )

  // --- Extract state ---
  const [extractResult, setExtractResult] = useState<
    AsyncState<{ extracted: number; clusters: number; applied: number; proposed: number; skipped?: boolean } | null>
  >({ data: null, loading: false, error: null })

  const extractNow = useCallback(async () => {
    setExtractResult({ data: null, loading: true, error: null })
    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        ...csrfHeaders(),
      }
      const resp = await fetch(`${API_BASE}/extract`, {
        method: 'POST',
        headers,
        credentials: 'same-origin',
      })
      if (!resp.ok) {
        const detail = await resp
          .json()
          .then((b) => b.detail)
          .catch(() => resp.statusText)
        throw new Error(typeof detail === 'string' ? detail : resp.statusText)
      }
      const data = await resp.json()
      setExtractResult({ data, loading: false, error: null })
      setRefreshKey((k) => k + 1)
    } catch (e: unknown) {
      setExtractResult({
        data: null,
        loading: false,
        error: e instanceof Error ? e.message : 'Unknown error',
      })
    }
  }, [])

  const fetchTrend = useCallback(
    (id: number, days: number = 30): Promise<TrendPoint[]> =>
      fetchJson<TrendPoint[]>(`${API_BASE}/${id}/trend?days=${days}`),
    [],
  )

  const fetchSignals = useCallback(
    (id: number): Promise<EvolutionSignals> =>
      fetchJson<EvolutionSignals>(`${API_BASE}/${id}/signals`),
    [],
  )

  const refetch = useCallback(() => {
    setRefreshKey((k) => k + 1)
  }, [])

  return useMemo(
    () => ({
      overview,
      stats,
      instincts,
      observations,
      extractResult,
      extractNow,
      fetchDetail,
      fetchDiff,
      fetchTrend,
      fetchSignals,
      review,
      fetchStats,
      fetchInstincts,
      fetchObservations,
      fetchSessionMessages,
      refetch,
    }),
    [overview, stats, instincts, observations, extractResult, extractNow,
     fetchDetail, fetchDiff, fetchTrend, fetchSignals, review,
     fetchStats, fetchInstincts, fetchObservations, fetchSessionMessages, refetch],
  )
}

import { useEffect, useState } from 'react'
import type {
  EvolutionDetail as EvoDetail,
  EvolutionApi,
} from '../../hooks/useEvolutionApi'
import ScoreTrendChart from './ScoreTrendChart'
import SignalBreakdown from './SignalBreakdown'
import VersionDiff from './VersionDiff'

interface Props {
  evolutionId: number
  api: EvolutionApi
}

export default function EvolutionDetailPanel({ evolutionId, api }: Props) {
  const [data, setData] = useState<EvoDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState(false)

  const load = () => {
    setLoading(true)
    setError(null)
    api
      .fetchDetail(evolutionId)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : 'Unknown error'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .fetchDetail(evolutionId)
      .then((data) => {
        if (!cancelled) setData(data)
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Unknown error')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [evolutionId])

  const handleReview = async (decision: 'keep' | 'rollback' | 'discard') => {
    setActionLoading(true)
    setError(null)
    try {
      await api.review(evolutionId, decision)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setActionLoading(false)
    }
  }

  if (loading) return <div className="evo-loading">Loading...</div>
  if (error) return <div className="evo-error">{error}</div>
  if (!data) return <div className="evo-empty">Not found</div>

  return (
    <div className="evo-detail">
      <h3>
        {data.skill_name}: v{data.from_version} → v{data.to_version}
      </h3>
      <p className="evo-meta">
        Source: {data.source} | Status: {data.status} | Created:{' '}
        {new Date(data.created_at * 1000).toLocaleString()}
      </p>
      {data.evolve_reason && (
        <p className="evo-reason">{data.evolve_reason}</p>
      )}

      {data.instincts && data.instincts.length > 0 && (
        <div className="evo-instincts">
          <h3>Source Instincts ({data.instincts.length})</h3>
          <div className="instinct-list">
            {data.instincts.map((inst) => (
              <div key={inst.id} className="instinct-item">
                <span className="evo-badge">{inst.normalized_trigger}</span>
                <span className="instinct-confidence">
                  {(inst.confidence * 100).toFixed(0)}%
                </span>
                <p className="instinct-desc">{inst.trigger} → {inst.action}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {data.status === 'proposed' && data.proposed_content && (
        <div className="evo-proposed">
          <h4>Proposed SKILL.md Content</h4>
          <pre className="evo-proposed-content">
            <code>{data.proposed_content}</code>
          </pre>
        </div>
      )}

      {(data.snapshots?.length ?? 0) > 0 && (
        <>
          <ScoreTrendChart
            snapshots={data.snapshots!}
            baseline={data.baseline_composite}
          />
          {data.signal_breakdown && (
            <SignalBreakdown breakdown={data.signal_breakdown} />
          )}
        </>
      )}

      <VersionDiff evolutionId={evolutionId} api={api} />

      {data.status === 'proposed' && (
        <div className="evo-actions">
          <button
            className="btn-keep"
            disabled={actionLoading}
            onClick={() => handleReview('keep')}
          >
            Approve & Apply
          </button>
          <button
            className="btn-discard"
            disabled={actionLoading}
            onClick={() => handleReview('discard')}
          >
            Discard
          </button>
        </div>
      )}

      {data.status === 'under_review' && (
        <div className="evo-actions">
          <button
            className="btn-keep"
            disabled={actionLoading}
            onClick={() => handleReview('keep')}
          >
            Keep
          </button>
          <button
            className="btn-rollback"
            disabled={actionLoading}
            onClick={() => handleReview('rollback')}
          >
            Rollback
          </button>
        </div>
      )}

    </div>
  )
}

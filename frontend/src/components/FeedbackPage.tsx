import { useState, useEffect } from 'react'

interface FeedbackItem {
  id: number
  skill_name: string
  user_id: string
  session_id: string | null
  rating: number
  comment: string
  user_edits: string
  skill_version: string
  timestamp: number
}

interface FeedbackStats {
  skill_name: string
  count: number
  avg_rating: number
}

interface FeedbackData {
  stats: FeedbackStats[]
  items: FeedbackItem[]
  total_count: number
}

interface FeedbackPageProps {
  userId: string
  authToken?: string | null
  onBack: () => void
}

function renderStars(rating: number): string {
  const filled = Math.round(rating)
  return '\u2605'.repeat(filled) + '\u2606'.repeat(5 - filled)
}

function formatDate(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function FeedbackPage({ userId: _userId, authToken, onBack }: FeedbackPageProps) {
  const [data, setData] = useState<FeedbackData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const headers: Record<string, string> = {}
    if (authToken) headers['Authorization'] = `Bearer ${authToken}`

    fetch('/api/admin/feedback', { headers })
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json()
      })
      .then(setData)
      .catch(err => {
        setError(err instanceof Error ? err.message : 'Failed to load feedback')
      })
      .finally(() => setLoading(false))
  }, [authToken])

  if (loading) {
    return (
      <div className="feedback-page">
        <div className="feedback-header">
          <button className="feedback-back-btn" onClick={onBack} type="button">
            &larr; Back
          </button>
          <h2>Feedback Management</h2>
        </div>
        <div className="feedback-loading">Loading...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="feedback-page">
        <div className="feedback-header">
          <button className="feedback-back-btn" onClick={onBack} type="button">
            &larr; Back
          </button>
          <h2>Feedback Management</h2>
        </div>
        <div className="feedback-error">{error}</div>
      </div>
    )
  }

  if (!data || data.total_count === 0) {
    return (
      <div className="feedback-page">
        <div className="feedback-header">
          <button className="feedback-back-btn" onClick={onBack} type="button">
            &larr; Back
          </button>
          <h2>Feedback Management</h2>
        </div>
        <div className="feedback-empty">No feedback submitted yet.</div>
      </div>
    )
  }

  return (
    <div className="feedback-page">
      <div className="feedback-header">
        <button className="feedback-back-btn" onClick={onBack} type="button">
          &larr; Back
        </button>
        <h2>Feedback Management</h2>
      </div>

      {/* Stats Section */}
      <div className="feedback-section">
        <h3 className="feedback-section-title">Feedback Stats</h3>
        <p className="feedback-total">{data.total_count} feedback(s) submitted</p>
        <table className="feedback-stats-table">
          <thead>
            <tr>
              <th>Skill</th>
              <th>Avg Rating</th>
              <th>Count</th>
              <th>Distribution</th>
            </tr>
          </thead>
          <tbody>
            {data.stats.map(s => (
              <tr key={s.skill_name}>
                <td className="feedback-skill-name">{s.skill_name}</td>
                <td className="feedback-rating">{renderStars(s.avg_rating)} {s.avg_rating.toFixed(1)}</td>
                <td>{s.count}</td>
                <td>
                  <div className="feedback-bar">
                    <div
                      className="feedback-bar-fill"
                      style={{ width: `${(s.avg_rating / 5) * 100}%` }}
                    />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Details Section */}
      <div className="feedback-section">
        <h3 className="feedback-section-title">Feedback Details</h3>
        <div className="feedback-items">
          {data.items.map(item => (
            <div key={item.id} className="feedback-item">
              <div className="feedback-item-header">
                <span className="feedback-item-skill">[{item.skill_name}]</span>
                <span className="feedback-item-rating">{renderStars(item.rating)}</span>
                <span className="feedback-item-date">{formatDate(item.timestamp)}</span>
              </div>
              {item.comment && (
                <p className="feedback-item-comment">&ldquo;{item.comment}&rdquo;</p>
              )}
              {item.session_id && (
                <p className="feedback-item-session">
                  Session: {item.session_id.slice(0, 30)}...
                </p>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

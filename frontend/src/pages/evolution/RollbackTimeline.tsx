import { useState, useEffect } from 'react'

interface TimelineEvent {
  date: string
  event: string
}

interface Props {
  evolutionId: number
}

export default function RollbackTimeline({ evolutionId }: Props) {
  const [events, setEvents] = useState<TimelineEvent[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetch(`/api/admin/skills/evolution-timeline/${evolutionId}`, {
      headers: {
        Authorization: `Bearer ${localStorage.getItem('authToken') || ''}`,
      },
    })
      .then((r) => {
        if (!r.ok) throw new Error('Not found')
        return r.json()
      })
      .then((data) => {
        if (!cancelled) setEvents(data.events || [])
      })
      .catch(() => {
        if (!cancelled) setEvents([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [evolutionId])

  if (loading) return <div className="evo-loading">Loading timeline...</div>

  return (
    <div className="evo-timeline">
      <h4>Rollback Timeline</h4>
      {events.length === 0 ? (
        <div className="timeline-placeholder">
          No timeline events recorded yet.
        </div>
      ) : (
        <div className="timeline-list">
          {events.map((ev, i) => (
            <div key={`${ev.date}-${i}`} className="timeline-item">
              <span className="timeline-date">{ev.date}</span>
              <span className="timeline-event">{ev.event}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

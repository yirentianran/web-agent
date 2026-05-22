import { useState, useEffect } from 'react'
import type { EvolutionApi } from '../../hooks/useEvolutionApi'

interface TimelineEvent {
  date: string
  event: string
}

interface Props {
  evolutionId: number
  api: EvolutionApi
}

export default function RollbackTimeline({ evolutionId }: Props) {
  const [events, setEvents] = useState<TimelineEvent[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
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
      .then((data) => setEvents(data.events || []))
      .catch(() => setEvents([]))
      .finally(() => setLoading(false))
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
            <div key={i} className="timeline-item">
              <span className="timeline-date">{ev.date}</span>
              <span className="timeline-event">{ev.event}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

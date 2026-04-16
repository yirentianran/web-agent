import { useEffect, useState } from 'react'
import './StatusSpinner.css'

const STALE_THRESHOLD_SEC = 30

interface StatusSpinnerProps {
  text?: string
  detail?: string
  variant?: 'default' | 'hook' | 'agent'
  startTime?: number
}

export default function StatusSpinner({ text, detail, variant = 'default', startTime }: StatusSpinnerProps) {
  const displayText = text || 'Working...'
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    if (!startTime) return
    setElapsed(0)
    const interval = setInterval(() => {
      setElapsed(Date.now() - startTime)
    }, 1000)
    return () => clearInterval(interval)
  }, [startTime])

  const isStale = elapsed > STALE_THRESHOLD_SEC * 1000
  const elapsedSec = Math.floor(elapsed / 1000)

  return (
    <div className={`status-spinner status-spinner--${variant}${isStale ? ' status-spinner--stale' : ''}`}>
      <div className="status-spinner__dots">
        <span /><span /><span />
      </div>
      <span className="status-spinner__text">
        {displayText}
        {detail && <strong className="status-spinner__detail">{detail}</strong>}
        {startTime !== undefined && (
          <span className="status-spinner__elapsed" data-stale={isStale}>
            {elapsedSec}s
          </span>
        )}
      </span>
    </div>
  )
}
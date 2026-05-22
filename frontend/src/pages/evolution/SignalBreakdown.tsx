import type { SignalBreakdown as SB } from '../../hooks/useEvolutionApi'

interface Props {
  breakdown: SB
}

function DeltaIndicator({ deltaPct }: { deltaPct: number }) {
  const isPositive = deltaPct >= 0
  return (
    <div className={`signal-delta ${isPositive ? 'positive' : 'negative'}`}>
      {isPositive ? '↑' : '↓'} {Math.abs(deltaPct).toFixed(1)}%
    </div>
  )
}

export default function SignalBreakdown({ breakdown }: Props) {
  return (
    <div className="signal-breakdown">
      <div className="signal-card">
        <h5>User Rating</h5>
        <div className="signal-value">
          {breakdown.rating.current.toFixed(1)} / 5
        </div>
        <DeltaIndicator deltaPct={breakdown.rating.delta_pct} />
      </div>
      <div className="signal-card">
        <h5>Usage</h5>
        <div className="signal-value">{breakdown.usage.current} / day</div>
        <DeltaIndicator deltaPct={breakdown.usage.delta_pct} />
      </div>
      <div className="signal-card">
        <h5>Session Success</h5>
        <div className="signal-value">
          {(breakdown.session_success.current * 100).toFixed(0)}%
        </div>
        <DeltaIndicator deltaPct={breakdown.session_success.delta_pct} />
      </div>
    </div>
  )
}

import type { EvolutionSignals } from '../../hooks/useEvolutionApi'

interface Props {
  signals: EvolutionSignals
}

function DeltaIndicator({ deltaPct }: { deltaPct: number }) {
  const isPositive = deltaPct >= 0
  return (
    <div className={`signal-delta ${isPositive ? 'positive' : 'negative'}`}>
      {isPositive ? '↑' : '↓'} {Math.abs(deltaPct).toFixed(1)}%
    </div>
  )
}

export default function SignalBreakdown({ signals }: Props) {
  return (
    <div className="signal-breakdown">
      <div className="signal-card">
        <h5>Tool Success Rate</h5>
        <div className="signal-value">
          {(signals.success_rate.current * 100).toFixed(1)}%
        </div>
        <DeltaIndicator deltaPct={signals.success_rate.delta_pct} />
        <div className="signal-baseline">
          Baseline: {(signals.success_rate.baseline * 100).toFixed(1)}%
        </div>
      </div>
      <div className="signal-card">
        <h5>Usage</h5>
        <div className="signal-value">{signals.usage_count.current} / day</div>
        <DeltaIndicator deltaPct={signals.usage_count.delta_pct} />
        <div className="signal-baseline">
          Baseline: {signals.usage_count.baseline} / day
        </div>
      </div>
    </div>
  )
}

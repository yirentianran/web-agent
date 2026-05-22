import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts'
import type { Snapshot } from '../../hooks/useEvolutionApi'

interface Props {
  snapshots: Snapshot[]
  baseline?: number | null
}

export default function ScoreTrendChart({ snapshots, baseline }: Props) {
  const baselineValue = baseline ?? 0.6
  const data = snapshots.map((s) => ({
    date: s.snapshot_date,
    score: s.composite_score,
    baseline: baselineValue,
  }))

  return (
    <div className="evo-chart">
      <h4>Composite Score Trend</h4>
      <ResponsiveContainer width="100%" height={250}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="date" />
          <YAxis domain={[0, 1]} />
          <Tooltip />
          <Line
            type="monotone"
            dataKey="score"
            stroke="#3b82f6"
            name="Current"
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="baseline"
            stroke="#9ca3af"
            strokeDasharray="5 5"
            name="Baseline"
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

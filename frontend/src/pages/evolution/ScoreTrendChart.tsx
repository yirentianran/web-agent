import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts'
import type { TrendPoint } from '../../hooks/useEvolutionApi'

interface Props {
  data: TrendPoint[]
}

export default function ScoreTrendChart({ data }: Props) {
  const chartData = data.map((d) => ({
    date: d.date,
    successRate: d.success_rate * 100,
    usage: d.usage_count,
  }))

  return (
    <div className="evo-chart">
      <h4>Success Rate Trend</h4>
      <ResponsiveContainer width="100%" height={250}>
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="date" />
          <YAxis yAxisId="left" domain={[0, 100]} />
          <YAxis yAxisId="right" orientation="right" />
          <Tooltip />
          <Line
            yAxisId="left"
            type="monotone"
            dataKey="successRate"
            stroke="#3b82f6"
            name="Success Rate %"
            dot={false}
          />
          <Line
            yAxisId="right"
            type="monotone"
            dataKey="usage"
            stroke="#10b981"
            name="Usage Count"
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

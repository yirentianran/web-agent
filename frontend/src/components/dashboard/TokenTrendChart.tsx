import { memo, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import type { DailyTokens } from "../../hooks/useDashboardApi";

interface TokenTrendChartProps {
  data: DailyTokens[];
  loading: boolean;
  error: string | null;
}

export default memo(function TokenTrendChart({ data, loading, error }: TokenTrendChartProps) {
  const { t } = useTranslation();

  const logged = useRef(false);
  useEffect(() => {
    if (!loading && data.length > 0 && !logged.current) {
      console.log(`[Dashboard] TokenTrendChart render with ${data.length} points at ${performance.now().toFixed(0)}ms`);
      logged.current = true;
    }
  });

  if (error) {
    return <div className="chart-error">{t("dashboard.chart.tokenLoadFailed", { error })}</div>;
  }

  if (loading) {
    return <div className="chart-loading">{t("dashboard.chart.loading")}</div>;
  }

  if (data.length === 0) {
    return <div className="chart-empty">{t("dashboard.chart.noTokenData")}</div>;
  }

  return (
    <div className="dashboard-chart">
      <h3 className="chart-title">{t("dashboard.chart.tokenTrends")}</h3>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
          <XAxis dataKey="date" tick={{ fontSize: 12 }} />
          <YAxis tick={{ fontSize: 12 }} tickFormatter={(v: number) => `${(v / 1000).toFixed(0)}K`} />
          <Tooltip
            formatter={(value) => [Number(value).toLocaleString(), undefined] as [string, undefined]}
            labelStyle={{ fontSize: 12 }}
          />
          <Legend />
          <Line type="monotone" dataKey="input" name={t("dashboard.chart.input")} stroke="#4f46e5" strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="output" name={t("dashboard.chart.output")} stroke="#16a34a" strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="cache_read" name={t("dashboard.chart.cacheRead")} stroke="#f59e0b" strokeWidth={2} dot={false} strokeDasharray="4 4" />
          <Line type="monotone" dataKey="cache_write" name={t("dashboard.chart.cacheWrite")} stroke="#dc2626" strokeWidth={2} dot={false} strokeDasharray="2 2" />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
});

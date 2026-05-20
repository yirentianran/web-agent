import { useEffect, useState, type ComponentType } from "react";
import { useTranslation } from "react-i18next";
import type { DailyTokens } from "../../hooks/useDashboardApi";

interface TokenTrendChartProps {
  data: DailyTokens[];
  loading: boolean;
  error: string | null;
}

export default function TokenTrendChart({ data, loading, error }: TokenTrendChartProps) {
  const { t } = useTranslation();
  const [ChartComponents, setChartComponents] = useState<{
    LineChart: ComponentType<any>;
    Line: ComponentType<any>;
    XAxis: ComponentType<any>;
    YAxis: ComponentType<any>;
    Tooltip: ComponentType<any>;
    ResponsiveContainer: ComponentType<any>;
    Legend: ComponentType<any>;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    import("recharts").then((mod) => {
      if (!cancelled) {
        setChartComponents({
          LineChart: mod.LineChart,
          Line: mod.Line,
          XAxis: mod.XAxis,
          YAxis: mod.YAxis,
          Tooltip: mod.Tooltip,
          ResponsiveContainer: mod.ResponsiveContainer,
          Legend: mod.Legend,
        });
      }
    });
    return () => { cancelled = true; };
  }, []);

  if (error) {
    return <div className="chart-error">{t("dashboard.chart.tokenLoadFailed", { error })}</div>;
  }

  if (loading || !ChartComponents) {
    return <div className="chart-loading">{t("dashboard.chart.loading")}</div>;
  }

  if (data.length === 0) {
    return <div className="chart-empty">{t("dashboard.chart.noTokenData")}</div>;
  }

  const { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } = ChartComponents;

  return (
    <div className="dashboard-chart">
      <h3 className="chart-title">{t("dashboard.chart.tokenTrends")}</h3>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
          <XAxis dataKey="date" tick={{ fontSize: 12 }} />
          <YAxis tick={{ fontSize: 12 }} tickFormatter={(v: number) => `${(v / 1000).toFixed(0)}K`} />
          <Tooltip
            formatter={(value: number) => [value.toLocaleString(), undefined]}
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
}

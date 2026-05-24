import { memo, useEffect, useMemo, useRef } from "react";
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
import type { DailyCount } from "../../hooks/useDashboardApi";

interface ActivityTrendChartProps {
  dauData: DailyCount[];
  sessionsData: DailyCount[];
  loading: boolean;
  error: string | null;
}

interface MergedPoint {
  date: string;
  dau: number;
  sessions: number;
}

export default memo(function ActivityTrendChart({
  dauData,
  sessionsData,
  loading,
  error,
}: ActivityTrendChartProps) {
  const { t } = useTranslation();

  const logged = useRef(false);
  useEffect(() => {
    if (!loading && (dauData.length > 0 || sessionsData.length > 0) && !logged.current) {
      console.log(`[Dashboard] ActivityTrendChart render with ${dauData.length}dau + ${sessionsData.length}sessions at ${performance.now().toFixed(0)}ms`);
      logged.current = true;
    }
  });

  const merged: MergedPoint[] = useMemo(() => {
    if (dauData.length === 0 && sessionsData.length === 0) return [];

    const map = new Map<string, MergedPoint>();

    for (const s of sessionsData) {
      map.set(s.date, { date: s.date, dau: 0, sessions: s.count });
    }

    for (const d of dauData) {
      const existing = map.get(d.date);
      if (existing) {
        existing.dau = d.count;
      } else {
        map.set(d.date, { date: d.date, dau: d.count, sessions: 0 });
      }
    }

    return Array.from(map.values()).sort((a, b) => a.date.localeCompare(b.date));
  }, [dauData, sessionsData]);

  if (error) {
    return (
      <div className="chart-error">
        {t("dashboard.chart.activityLoadFailed", { error })}
      </div>
    );
  }

  if (loading) {
    return <div className="chart-loading">{t("dashboard.chart.loading")}</div>;
  }

  if (merged.length === 0) {
    return (
      <div className="chart-empty">{t("dashboard.chart.noActivityData")}</div>
    );
  }

  return (
    <div className="dashboard-chart">
      <h3 className="chart-title">{t("dashboard.chart.activityTrends")}</h3>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart
          data={merged}
          margin={{ top: 5, right: 20, bottom: 5, left: 0 }}
        >
          <XAxis dataKey="date" tick={{ fontSize: 12 }} />
          <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
          <Tooltip labelStyle={{ fontSize: 12 }} />
          <Legend />
          <Line
            type="monotone"
            dataKey="dau"
            name={t("dashboard.chart.dau")}
            stroke="#4f46e5"
            strokeWidth={2}
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="sessions"
            name={t("dashboard.chart.sessions")}
            stroke="#16a34a"
            strokeWidth={2}
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
});

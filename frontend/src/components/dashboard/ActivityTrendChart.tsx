import { useEffect, useState, type ComponentType } from "react";
import { useTranslation } from "react-i18next";
import type { DailyCount } from "../../hooks/useDashboardApi";

interface ActivityTrendChartProps {
  dauData: DailyCount[];
  sessionsData: DailyCount[];
  loading: boolean;
  error: string | null;
}

interface ChartComponents {
  LineChart: ComponentType<any>;
  Line: ComponentType<any>;
  XAxis: ComponentType<any>;
  YAxis: ComponentType<any>;
  Tooltip: ComponentType<any>;
  ResponsiveContainer: ComponentType<any>;
  Legend: ComponentType<any>;
}

interface MergedPoint {
  date: string;
  dau: number;
  sessions: number;
}

export default function ActivityTrendChart({
  dauData,
  sessionsData,
  loading,
  error,
}: ActivityTrendChartProps) {
  const { t } = useTranslation();
  const [ChartComponents, setChartComponents] = useState<ChartComponents | null>(null);

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
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <div className="chart-error">
        {t("dashboard.chart.activityLoadFailed", { error })}
      </div>
    );
  }

  if (loading || !ChartComponents) {
    return <div className="chart-loading">{t("dashboard.chart.loading")}</div>;
  }

  if (dauData.length === 0 && sessionsData.length === 0) {
    return (
      <div className="chart-empty">{t("dashboard.chart.noActivityData")}</div>
    );
  }

  // Merge DAU and session data by date
  const merged: MergedPoint[] = dauData.map((d) => {
    const session = sessionsData.find((s) => s.date === d.date);
    return { date: d.date, dau: d.count, sessions: session?.count ?? 0 };
  });

  // Include dates that only appear in sessionsData
  if (merged.length === 0 && sessionsData.length > 0) {
    sessionsData.forEach((s) => {
      if (!merged.find((m) => m.date === s.date)) {
        merged.push({ date: s.date, dau: 0, sessions: s.count });
      }
    });
  }

  const {
    LineChart,
    Line,
    XAxis,
    YAxis,
    Tooltip,
    ResponsiveContainer,
    Legend,
  } = ChartComponents;

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
}

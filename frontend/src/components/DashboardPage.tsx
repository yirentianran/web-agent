import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useDashboardApi, type OverviewData } from "../hooks/useDashboardApi";
import { formatDate, daysAgoStr, todayStr } from "../lib/dates";
import TimeRangeSelector from "./dashboard/TimeRangeSelector";
import OverviewCards from "./dashboard/OverviewCards";
import TokenTrendChart from "./dashboard/TokenTrendChart";
import ActivityTrendChart from "./dashboard/ActivityTrendChart";
import UserRankingTable from "./dashboard/UserRankingTable";
import SkillRankingTable from "./dashboard/SkillRankingTable";
import ResourcePanel from "./dashboard/ResourcePanel";
import "./dashboard/dashboard.css";

export default function DashboardPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [timeRange, setTimeRange] = useState({ from: daysAgoStr(30), to: todayStr() });

  const api = useDashboardApi(timeRange.from, timeRange.to);

  // Fetch previous period for deltas (same length, anchored to range start)
  const rangeDays = Math.round(
    (new Date(timeRange.to).getTime() - new Date(timeRange.from).getTime()) /
      (1000 * 60 * 60 * 24),
  );
  const prevFrom = formatDate(
    new Date(new Date(timeRange.from).getTime() - rangeDays * 24 * 60 * 60 * 1000),
  );
  const prevTo = formatDate(
    new Date(new Date(timeRange.from).getTime() - 24 * 60 * 60 * 1000),
  );

  const [prevOverview, setPrevOverview] = useState<OverviewData | null>(null);

  useEffect(() => {
    const abort = new AbortController();
    const token = localStorage.getItem("authToken") || "";
    const params = `?from_date=${prevFrom}&to_date=${prevTo}`;
    fetch(`/api/admin/dashboard/overview${params}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal: abort.signal,
    })
      .then((r) => r.json())
      .then(setPrevOverview)
      .catch(() => setPrevOverview(null));
    return () => abort.abort();
  }, [prevFrom, prevTo]);

  function handleTimeChange(from: string, to: string) {
    setTimeRange({ from, to });
    api.refetch(from, to);
  }

  return (
    <div className="dashboard-page">
      <button className="dashboard-back" onClick={() => navigate("/")}>
        {t("common.back")}
      </button>

      <div className="dashboard-header">
        <h2>{t("dashboard.title")}</h2>
        <TimeRangeSelector
          from={timeRange.from}
          to={timeRange.to}
          onChange={handleTimeChange}
        />
      </div>

      <OverviewCards
        data={api.overview.data}
        previousData={prevOverview}
        loading={api.overview.loading}
        error={api.overview.error}
      />

      <TokenTrendChart
        data={api.trends.data?.tokens ?? []}
        loading={api.trends.loading}
        error={api.trends.error}
      />

      <ActivityTrendChart
        dauData={api.trends.data?.active_users ?? []}
        sessionsData={api.trends.data?.sessions ?? []}
        loading={api.trends.loading}
        error={api.trends.error}
      />

      <div className="rankings-row">
        <UserRankingTable
          data={api.rankings.data?.top_users ?? []}
          loading={api.rankings.loading}
          error={api.rankings.error}
        />
        <SkillRankingTable
          data={api.rankings.data?.top_skills ?? []}
          loading={api.rankings.loading}
          error={api.rankings.error}
        />
      </div>

      <ResourcePanel />
    </div>
  );
}

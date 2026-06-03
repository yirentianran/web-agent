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

const EMPTY_DAILY_COUNT: import("../hooks/useDashboardApi").DailyCount[] = [];
const EMPTY_DAILY_TOKENS: import("../hooks/useDashboardApi").DailyTokens[] = [];
const EMPTY_TOP_USER: import("../hooks/useDashboardApi").TopUser[] = [];
const EMPTY_TOP_SKILL: import("../hooks/useDashboardApi").TopSkill[] = [];

export default function DashboardPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [timeRange, setTimeRange] = useState({ from: daysAgoStr(7), to: todayStr() });

  useEffect(() => {
    performance.mark('dashboard-enter');
    console.log('[Dashboard] Page mounted, fetch starting...');
  }, []);

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
    <div className="dashboard-page detail-page">
      <div className="evolution-header skills-header detail-header">
        <button
          className="evolution-back-btn skills-back-btn detail-back-btn"
          onClick={() => navigate("/")}
          type="button"
        >
          {t("common.back")}
        </button>
        <div className="evolution-header-title-group skills-header-title-group">
          <TimeRangeSelector
            from={timeRange.from}
            to={timeRange.to}
            onChange={handleTimeChange}
          />
          <h2>{t("dashboard.title")}</h2>
        </div>
      </div>

      <OverviewCards
        data={api.overview.data}
        previousData={prevOverview}
        loading={api.overview.loading}
        error={api.overview.error}
      />

      <TokenTrendChart
        data={api.trends.data?.tokens ?? EMPTY_DAILY_TOKENS}
        loading={api.trends.loading}
        error={api.trends.error}
      />

      <ActivityTrendChart
        dauData={api.trends.data?.active_users ?? EMPTY_DAILY_COUNT}
        sessionsData={api.trends.data?.sessions ?? EMPTY_DAILY_COUNT}
        loading={api.trends.loading}
        error={api.trends.error}
      />

      <div className="rankings-row">
        <UserRankingTable
          data={api.rankings.data?.top_users ?? EMPTY_TOP_USER}
          loading={api.rankings.loading}
          error={api.rankings.error}
        />
        <SkillRankingTable
          data={api.rankings.data?.top_skills ?? EMPTY_TOP_SKILL}
          loading={api.rankings.loading}
          error={api.rankings.error}
        />
      </div>

      <ResourcePanel />
    </div>
  );
}

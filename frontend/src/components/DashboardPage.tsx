import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useDashboardApi } from "../hooks/useDashboardApi";
import TimeRangeSelector from "./dashboard/TimeRangeSelector";
import OverviewCards from "./dashboard/OverviewCards";
import TokenTrendChart from "./dashboard/TokenTrendChart";
import ActivityTrendChart from "./dashboard/ActivityTrendChart";
import UserRankingTable from "./dashboard/UserRankingTable";
import SkillRankingTable from "./dashboard/SkillRankingTable";
import ResourcePanel from "./dashboard/ResourcePanel";
import "./dashboard/dashboard.css";

function formatDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function todayStr(): string {
  return formatDate(new Date());
}

function daysAgoStr(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return formatDate(d);
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const [timeRange, setTimeRange] = useState({ from: daysAgoStr(30), to: todayStr() });

  const api = useDashboardApi(timeRange.from, timeRange.to);

  // Fetch previous period for deltas (same length, before current range)
  const rangeDays = Math.round(
    (new Date(timeRange.to).getTime() - new Date(timeRange.from).getTime()) /
      (1000 * 60 * 60 * 24),
  );
  const prevFrom = daysAgoStr(rangeDays * 2);
  const prevTo = daysAgoStr(rangeDays + 1);

  const [prevOverview, setPrevOverview] = useState<any>(null);

  useEffect(() => {
    const token = localStorage.getItem("authToken") || "";
    const params = `?from_date=${prevFrom}&to_date=${prevTo}`;
    fetch(`/api/admin/dashboard/overview${params}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((r) => r.json())
      .then(setPrevOverview)
      .catch(() => setPrevOverview(null));
  }, [prevFrom, prevTo]);

  function handleTimeChange(from: string, to: string) {
    setTimeRange({ from, to });
    api.refetch(from, to);
  }

  return (
    <div className="dashboard-page">
      <button className="dashboard-back" onClick={() => navigate("/")}>
        ← Back
      </button>

      <div className="dashboard-header">
        <h2>Usage Dashboard</h2>
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
        data={api.trends.data?.daily_tokens ?? []}
        loading={api.trends.loading}
        error={api.trends.error}
      />

      <ActivityTrendChart
        dauData={api.trends.data?.daily_active_users ?? []}
        sessionsData={api.trends.data?.daily_sessions ?? []}
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

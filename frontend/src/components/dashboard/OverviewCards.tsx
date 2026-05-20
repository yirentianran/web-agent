import { useTranslation } from "react-i18next";
import type { OverviewData } from "../../hooks/useDashboardApi";
import "./OverviewCards.css";

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function formatDelta(current: number, previous: number): string {
  if (previous === 0) return "";
  const pct = ((current - previous) / previous) * 100;
  const sign = pct >= 0 ? "↑" : "↓";
  return `${sign}${Math.abs(pct).toFixed(0)}%`;
}

function deltaClass(current: number, previous: number): string {
  if (previous === 0) return "delta-neutral";
  return current >= previous ? "delta-up" : "delta-down";
}

interface OverviewCardsProps {
  data: OverviewData | null;
  previousData: OverviewData | null;
  loading: boolean;
  error: string | null;
}

export default function OverviewCards({ data, previousData, loading, error }: OverviewCardsProps) {
  const { t } = useTranslation();

  if (error) {
    return <div className="overview-error">{t("dashboard.overview.loadFailed", { error })}</div>;
  }

  const totalTokens = data
    ? data.total_input_tokens + data.total_output_tokens + data.total_cache_read_tokens + data.total_cache_write_tokens
    : 0;
  const prevTotalTokens = previousData
    ? previousData.total_input_tokens + previousData.total_output_tokens + previousData.total_cache_read_tokens + previousData.total_cache_write_tokens
    : 0;

  const cards = [
    {
      label: t("dashboard.overview.activeUsers"),
      value: loading ? "—" : String(data?.active_users ?? 0),
      delta: data && previousData ? formatDelta(data.active_users, previousData.active_users) : "",
      deltaCls: data && previousData ? deltaClass(data.active_users, previousData.active_users) : "",
    },
    {
      label: t("dashboard.overview.totalUsers"),
      value: loading ? "—" : String(data?.total_users ?? 0),
      delta: "",
      deltaCls: "",
    },
    {
      label: t("dashboard.overview.newUsers"),
      value: loading ? "—" : `+${data?.new_users ?? 0}`,
      delta: data && previousData ? formatDelta(data.new_users, previousData.new_users) : "",
      deltaCls: data && previousData ? deltaClass(data.new_users, previousData.new_users) : "",
    },
    {
      label: t("dashboard.overview.totalSessions"),
      value: loading ? "—" : String(data?.total_sessions ?? 0),
      delta: data && previousData ? formatDelta(data.total_sessions, previousData.total_sessions) : "",
      deltaCls: data && previousData ? deltaClass(data.total_sessions, previousData.total_sessions) : "",
    },
    {
      label: t("dashboard.overview.tokenUsage"),
      value: loading ? "—" : formatTokens(totalTokens),
      delta: data && previousData ? formatDelta(totalTokens, prevTotalTokens) : "",
      deltaCls: data && previousData ? deltaClass(totalTokens, prevTotalTokens) : "",
      detail: data ? `I ${formatTokens(data.total_input_tokens)}  O ${formatTokens(data.total_output_tokens)}` : "",
    },
  ];

  return (
    <div className="overview-cards">
      {cards.map((card) => (
        <div key={card.label} className={`overview-card ${loading ? "loading" : ""}`}>
          <div className="card-label">{card.label}</div>
          <div className="card-value">{card.value}</div>
          {card.detail && <div className="card-detail">{card.detail}</div>}
          {card.delta && <span className={`card-delta ${card.deltaCls}`}>{card.delta}</span>}
        </div>
      ))}
    </div>
  );
}

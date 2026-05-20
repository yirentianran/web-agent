import type { TopUser } from "../../hooks/useDashboardApi";

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

interface UserRankingTableProps {
  data: TopUser[];
  loading: boolean;
  error: string | null;
}

export default function UserRankingTable({ data, loading, error }: UserRankingTableProps) {
  if (error) {
    return <div className="ranking-error">Failed to load user rankings: {error}</div>;
  }

  return (
    <div className="ranking-panel">
      <h3 className="ranking-title">Top Users by Token Consumption</h3>
      {loading ? (
        <div className="ranking-loading">Loading...</div>
      ) : data.length === 0 ? (
        <div className="ranking-empty">No data for selected period</div>
      ) : (
        <table className="ranking-table">
          <thead>
            <tr>
              <th>#</th>
              <th>User</th>
              <th className="right">Tokens</th>
              <th className="right">Sessions</th>
            </tr>
          </thead>
          <tbody>
            {data.map((user, i) => (
              <tr key={user.user_id}>
                <td className="rank">{i + 1}</td>
                <td>{user.user_id}</td>
                <td className="right mono">{formatTokens(user.total_tokens)}</td>
                <td className="right">{user.session_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

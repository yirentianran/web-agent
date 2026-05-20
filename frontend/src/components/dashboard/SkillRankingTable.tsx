import type { TopSkill } from "../../hooks/useDashboardApi";

interface SkillRankingTableProps {
  data: TopSkill[];
  loading: boolean;
  error: string | null;
}

export default function SkillRankingTable({ data, loading, error }: SkillRankingTableProps) {
  if (error) {
    return <div className="ranking-error">Failed to load skill rankings: {error}</div>;
  }

  return (
    <div className="ranking-panel">
      <h3 className="ranking-title">Top Skills by Usage</h3>
      {loading ? (
        <div className="ranking-loading">Loading...</div>
      ) : data.length === 0 ? (
        <div className="ranking-empty">No data for selected period</div>
      ) : (
        <table className="ranking-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Skill</th>
              <th className="right">Uses</th>
              <th className="right">Users</th>
            </tr>
          </thead>
          <tbody>
            {data.map((skill, i) => (
              <tr key={skill.skill_name}>
                <td className="rank">{i + 1}</td>
                <td>{skill.skill_name}</td>
                <td className="right">{skill.use_count}</td>
                <td className="right">{skill.unique_users}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

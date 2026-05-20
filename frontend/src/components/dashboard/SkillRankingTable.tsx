import { useTranslation } from "react-i18next";
import type { TopSkill } from "../../hooks/useDashboardApi";

interface SkillRankingTableProps {
  data: TopSkill[];
  loading: boolean;
  error: string | null;
}

export default function SkillRankingTable({ data, loading, error }: SkillRankingTableProps) {
  const { t } = useTranslation();

  if (error) {
    return <div className="ranking-error">{t("dashboard.ranking.skillLoadFailed", { error })}</div>;
  }

  return (
    <div className="ranking-panel">
      <h3 className="ranking-title">{t("dashboard.ranking.topSkills")}</h3>
      {loading ? (
        <div className="ranking-loading">{t("common.loading")}</div>
      ) : data.length === 0 ? (
        <div className="ranking-empty">{t("dashboard.ranking.noData")}</div>
      ) : (
        <table className="ranking-table">
          <thead>
            <tr>
              <th>{t("dashboard.ranking.rank")}</th>
              <th>{t("dashboard.ranking.skill")}</th>
              <th className="right">{t("dashboard.ranking.uses")}</th>
              <th className="right">{t("dashboard.ranking.users")}</th>
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

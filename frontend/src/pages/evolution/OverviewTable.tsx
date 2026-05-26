import { useTranslation } from 'react-i18next';
import type { EvolutionItem } from '../../hooks/useEvolutionApi'

interface Props {
  data: { items: EvolutionItem[]; total: number; page: number } | null
  loading: boolean
  error: string | null
  onRowClick: (item: EvolutionItem) => void
}

const STATUS_CLASSES: Record<string, string> = {
  active: 'status-active',
  proposed: 'status-proposed',
  under_review: 'status-review',
  rolled_back: 'status-rolled',
  superseded: 'status-rolled',
}

function ScoreBadge({ score }: { score: number | null | undefined }) {
  if (score == null) return <span className="score-na">—</span>
  const color = score >= 0.7 ? 'score-good' : score >= 0.5 ? 'score-warn' : 'score-bad'
  return <span className={`score-badge ${color}`}>{(score * 100).toFixed(0)}%</span>
}

export default function OverviewTable({
  data,
  loading,
  error,
  onRowClick,
}: Props) {
  const { t } = useTranslation();

  if (loading) return <div className="evo-loading">{t('common.loading')}</div>
  if (error) return <div className="evo-error">{error}</div>
  if (!data || data.items.length === 0) {
    return <div className="evo-empty">{t('evolutionMonitor.noEvolutions')}</div>
  }

  return (
    <table className="evo-table">
      <thead>
        <tr>
          <th>{t('evolutionMonitor.skill')}</th>
          <th>{t('evolutionMonitor.version')}</th>
          <th>{t('evolutionMonitor.instinctCount')}</th>
          <th>{t('evolutionMonitor.compositeScore')}</th>
          <th>{t('evolutionMonitor.daysActive')}</th>
          <th>{t('evolutionMonitor.source')}</th>
          <th>{t('evolutionMonitor.status')}</th>
          <th>{t('evolutionMonitor.created')}</th>
        </tr>
      </thead>
      <tbody>
        {data.items.map((item) => (
          <tr
            key={item.id}
            onClick={() => onRowClick(item)}
            className="evo-row"
          >
            <td>{item.skill_name}</td>
            <td>
              v{item.from_version} → v{item.to_version}
            </td>
            <td>{item.instinct_count ?? 0}</td>
            <td><ScoreBadge score={item.composite_score} /></td>
            <td>{item.days_active ?? 1}d</td>
            <td>{item.source}</td>
            <td>
              <span className={`evo-badge ${STATUS_CLASSES[item.status] || ''}`}>
                {item.status}
              </span>
            </td>
            <td>{new Date(item.created_at * 1000).toLocaleDateString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

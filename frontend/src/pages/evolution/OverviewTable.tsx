import { useTranslation } from 'react-i18next';
import type { EvolutionItem } from '../../hooks/useEvolutionApi'

interface Props {
  data: { items: EvolutionItem[]; total: number; page: number } | null
  loading: boolean
  error: string | null
  page: number
  pageSize: number
  onPageChange: (page: number) => void
  onRowClick: (item: EvolutionItem) => void
}

const pageBtnStyle: React.CSSProperties = {
  padding: '4px 10px', border: '1px solid var(--color-border)',
  borderRadius: 4, background: 'var(--color-surface)', cursor: 'pointer',
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
  page,
  pageSize,
  onPageChange,
  onRowClick,
}: Props) {
  const { t } = useTranslation();

  if (loading) return <div className="evo-loading">{t('common.loading')}</div>
  if (error) return <div className="evo-error">{error}</div>
  if (!data || data.items.length === 0) {
    return <div className="evo-empty">{t('evolutionMonitor.noEvolutions')}</div>
  }

  return (
    <>
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
      <Pagination page={page} total={data.total} pageSize={pageSize} onPageChange={onPageChange} />
    </>
  )
}

function Pagination({
  page, total, pageSize, onPageChange,
}: {
  page: number; total: number; pageSize: number; onPageChange: (p: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  if (totalPages <= 1) return null;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'center', padding: 16, fontSize: '0.85rem' }}>
      <button disabled={page <= 1} onClick={() => onPageChange(page - 1)} style={pageBtnStyle}>←</button>
      {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
        const start = Math.max(1, Math.min(page - 2, totalPages - 4));
        const p = start + i;
        if (p > totalPages) return null;
        return (
          <button
            key={p}
            onClick={() => onPageChange(p)}
            style={{
              ...pageBtnStyle,
              ...(p === page ? { background: 'var(--color-primary)', color: '#fff', borderColor: 'var(--color-primary)' } : {}),
            }}
          >
            {p}
          </button>
        );
      })}
      <button disabled={page >= totalPages} onClick={() => onPageChange(page + 1)} style={pageBtnStyle}>→</button>
      <span style={{ color: 'var(--color-muted)', marginLeft: 12 }}>{total} total</span>
    </div>
  );
}

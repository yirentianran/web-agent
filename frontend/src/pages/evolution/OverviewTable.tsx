import type { EvolutionItem } from '../../hooks/useEvolutionApi'

interface Props {
  data: { items: EvolutionItem[]; total: number; page: number } | null
  loading: boolean
  error: string | null
  onRowClick: (item: EvolutionItem) => void
}

const STATUS_LABELS: Record<string, { label: string; className: string }> = {
  active: { label: 'Active', className: 'status-active' },
  proposed: { label: 'Proposed', className: 'status-proposed' },
  under_review: { label: 'Under Review', className: 'status-review' },
  rolled_back: { label: 'Rolled Back', className: 'status-rolled' },
}

export default function OverviewTable({
  data,
  loading,
  error,
  onRowClick,
}: Props) {
  if (loading) return <div className="evo-loading">Loading...</div>
  if (error) return <div className="evo-error">{error}</div>
  if (!data || data.items.length === 0) {
    return <div className="evo-empty">No evolution records found.</div>
  }

  return (
    <table className="evo-table">
      <thead>
        <tr>
          <th>Skill</th>
          <th>Version</th>
          <th>Source</th>
          <th>Status</th>
          <th>Created</th>
        </tr>
      </thead>
      <tbody>
        {data.items.map((item) => {
          const s = STATUS_LABELS[item.status] || {
            label: item.status,
            className: '',
          }
          return (
            <tr
              key={item.id}
              onClick={() => onRowClick(item)}
              className="evo-row"
            >
              <td>{item.skill_name}</td>
              <td>
                v{item.from_version} → v{item.to_version}
              </td>
              <td>{item.source}</td>
              <td>
                <span className={`evo-badge ${s.className}`}>{s.label}</span>
              </td>
              <td>{new Date(item.created_at * 1000).toLocaleDateString()}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

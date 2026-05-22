import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useEvolutionApi, type EvolutionItem } from '../hooks/useEvolutionApi'
import OverviewTable from './evolution/OverviewTable'
import EvolutionDetail from './evolution/EvolutionDetail'
import './evolution/evolution.css'

type View = 'overview' | { detail: number }

export default function EvolutionPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [statusFilter, setStatusFilter] = useState<string | undefined>()
  const [view, setView] = useState<View>('overview')
  const api = useEvolutionApi(statusFilter)

  const handleRowClick = (item: EvolutionItem) => {
    setView({ detail: item.id })
  }

  if (typeof view === 'object' && 'detail' in view) {
    return (
      <div className="evolution-page">
        <button
          className="evolution-back"
          onClick={() => setView('overview')}
        >
          ← {t('common.back')}
        </button>
        <EvolutionDetail evolutionId={view.detail} api={api} />
      </div>
    )
  }

  return (
    <div className="evolution-page">
      <button className="evolution-back" onClick={() => navigate('/')}>
        ← {t('common.back')}
      </button>

      <div className="evolution-header">
        <h2>CI Evolution Monitor</h2>
        <div className="status-tabs">
          {['All', 'Active', 'Proposed', 'Under Review', 'Rolled Back'].map(
            (label) => {
              const value =
                label === 'All'
                  ? undefined
                  : label.toLowerCase().replace(' ', '_')
              return (
                <button
                  key={label}
                  className={`tab-btn ${statusFilter === value ? 'active' : ''}`}
                  onClick={() => setStatusFilter(value)}
                >
                  {label}
                </button>
              )
            },
          )}
        </div>
      </div>

      <OverviewTable
        data={api.overview.data}
        loading={api.overview.loading}
        error={api.overview.error}
        onRowClick={handleRowClick}
      />
    </div>
  )
}

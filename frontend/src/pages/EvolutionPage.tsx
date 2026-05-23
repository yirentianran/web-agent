import { useState, useEffect, useCallback } from 'react';
import { StatsCards } from './evolution/StatsCards';
import { PipelineFunnel } from './evolution/PipelineFunnel';
import OverviewTable from './evolution/OverviewTable';
import EvolutionDetail from './evolution/EvolutionDetail';
import { InstinctList } from './evolution/InstinctList';
import { ObservationBrowser } from './evolution/ObservationBrowser';
import { useEvolutionApi } from '../hooks/useEvolutionApi';
import './evolution/evolution.css';

type TabId = 'evolutions' | 'instincts' | 'observations';

export default function EvolutionPage() {
  const api = useEvolutionApi();
  const [activeTab, setActiveTab] = useState<TabId>('evolutions');
  const [detailId, setDetailId] = useState<number | null>(null);

  useEffect(() => {
    api.fetchStats();
    api.fetchInstincts({});
    api.fetchObservations({});
  }, []);

  const handleInstinctFilter = useCallback(
    (filters: { domain?: string; scope?: string }) => {
      api.fetchInstincts(filters);
    },
    [api.fetchInstincts]
  );

  const handleObsFilter = useCallback(
    (filters: { session_id?: string; event_type?: string }) => {
      api.fetchObservations(filters);
    },
    [api.fetchObservations]
  );

  if (detailId !== null) {
    return (
      <div className="evolution-page">
        <button className="evolution-back" onClick={() => setDetailId(null)}>
          ← Back to overview
        </button>
        <EvolutionDetail evolutionId={detailId} api={api} />
      </div>
    );
  }

  const TABS: { id: TabId; label: string }[] = [
    { id: 'evolutions', label: '进化列表' },
    { id: 'instincts', label: '本能列表' },
    { id: 'observations', label: '事件浏览' },
  ];

  return (
    <div className="evolution-page">
      <div className="evolution-header">
        <h1>Evolution Monitor</h1>
      </div>

      <StatsCards stats={api.stats.data ?? null} loading={api.stats.loading} />
      <PipelineFunnel stats={api.stats.data ?? null} />

      <div className="status-tabs">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            className={`tab-btn ${activeTab === id ? 'active' : ''}`}
            onClick={() => setActiveTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {activeTab === 'evolutions' && (
        <OverviewTable
          data={api.overview.data}
          loading={api.overview.loading}
          error={api.overview.error}
          onRowClick={(item) => setDetailId(item.id)}
        />
      )}
      {activeTab === 'instincts' && (
        <InstinctList
          data={api.instincts.data}
          loading={api.instincts.loading}
          error={api.instincts.error}
          onFilterChange={handleInstinctFilter}
        />
      )}
      {activeTab === 'observations' && (
        <ObservationBrowser
          data={api.observations.data}
          loading={api.observations.loading}
          error={api.observations.error}
          onFilterChange={handleObsFilter}
        />
      )}
    </div>
  );
}

import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { StatsCards } from './evolution/StatsCards';
import { PipelineFunnel } from './evolution/PipelineFunnel';
import OverviewTable from './evolution/OverviewTable';
import EvolutionDetail from './evolution/EvolutionDetail';
import { InstinctList } from './evolution/InstinctList';
import { ObservationBrowser } from './evolution/ObservationBrowser';
import { useEvolutionApi } from '../hooks/useEvolutionApi';
import './evolution/evolution.css';

type TabId = 'evolutions' | 'instincts' | 'observations';

const TIME_RANGES: { days: number; labelKey: string }[] = [
  { days: 0, labelKey: 'evolutionMonitor.timeToday' },
  { days: 7, labelKey: 'evolutionMonitor.time7Days' },
  { days: 30, labelKey: 'evolutionMonitor.time30Days' },
  { days: 90, labelKey: 'evolutionMonitor.timeAll' },
];

export default function EvolutionPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const api = useEvolutionApi();
  const [activeTab, setActiveTab] = useState<TabId>('evolutions');
  const [detailId, setDetailId] = useState<number | null>(null);
  const [timeRange, setTimeRange] = useState(0);
  const refreshRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadData = useCallback((days: number) => {
    api.fetchStats(days);
    api.fetchInstincts({});
    api.fetchObservations({});
  }, [api]);

  useEffect(() => {
    loadData(timeRange);
  }, [timeRange]);

  // Auto-refresh every 30s when not viewing detail
  useEffect(() => {
    if (detailId !== null) {
      if (refreshRef.current) {
        clearInterval(refreshRef.current);
        refreshRef.current = null;
      }
      return;
    }
    refreshRef.current = setInterval(() => loadData(timeRange), 30000);
    return () => {
      if (refreshRef.current) {
        clearInterval(refreshRef.current);
        refreshRef.current = null;
      }
    };
  }, [detailId, timeRange, loadData]);

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

  const TABS: { id: TabId; labelKey: string }[] = [
    { id: 'evolutions', labelKey: 'evolutionMonitor.evolutionsTab' },
    { id: 'instincts', labelKey: 'evolutionMonitor.instinctsTab' },
    { id: 'observations', labelKey: 'evolutionMonitor.observationsTab' },
  ];

  if (detailId !== null) {
    return (
      <div className="evolution-page detail-page">
        <div className="evolution-header skills-header detail-header">
          <button
            className="evolution-back-btn skills-back-btn detail-back-btn"
            onClick={() => setDetailId(null)}
            type="button"
          >
            {t('evolutionMonitor.backToOverview')}
          </button>
          <div className="evolution-header-title-group skills-header-title-group">
            <h2>{t('evolutionMonitor.title')}</h2>
          </div>
        </div>
        <EvolutionDetail evolutionId={detailId} api={api} />
      </div>
    );
  }

  return (
    <div className="evolution-page detail-page">
      <div className="evolution-header skills-header detail-header">
        <button
          className="evolution-back-btn skills-back-btn detail-back-btn"
          onClick={() => navigate('/')}
          type="button"
        >
          {t('common.back')}
        </button>
        <div className="evolution-header-title-group skills-header-title-group">
          <h2>{t('evolutionMonitor.title')}</h2>
        </div>
      </div>

      {/* Time range selector */}
      <div className="time-range-bar">
        {TIME_RANGES.map(({ days, labelKey }) => (
          <button
            key={days}
            className={`time-range-btn ${timeRange === days ? 'active' : ''}`}
            onClick={() => setTimeRange(days)}
          >
            {t(labelKey)}
          </button>
        ))}
        <span className="auto-refresh-indicator" title={t('evolutionMonitor.autoRefresh')}>
          {t('evolutionMonitor.autoRefresh')}
        </span>
      </div>

      <StatsCards stats={api.stats.data ?? null} loading={api.stats.loading} />
      <PipelineFunnel stats={api.stats.data ?? null} />

      <div className="skills-tabs">
        {TABS.map(({ id, labelKey }) => (
          <button
            key={id}
            className={`skills-tab ${activeTab === id ? 'active' : ''}`}
            onClick={() => setActiveTab(id)}
          >
            {t(labelKey)}
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

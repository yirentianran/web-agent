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
  const [timeRange, setTimeRange] = useState(7);
  const loadData = useCallback((days: number) => {
    api.fetchStats(days);
    api.fetchInstincts({});
    api.fetchObservations({});
  }, [api]);

  useEffect(() => {
    loadData(timeRange);
  }, [timeRange]);

  // Refresh data when extraction completes successfully
  const prevExtractLoading = useRef(api.extractResult.loading);
  useEffect(() => {
    if (prevExtractLoading.current && !api.extractResult.loading && !api.extractResult.error) {
      loadData(timeRange);
    }
    prevExtractLoading.current = api.extractResult.loading;
  }, [api.extractResult.loading, api.extractResult.error, timeRange, loadData]);

  const handleExtract = useCallback(() => {
    api.extractNow();
  }, [api.extractNow]);

  const extractMsg: string | null = api.extractResult.error
    ? api.extractResult.error
    : api.extractResult.data
      ? api.extractResult.data.skipped
        ? t('evolutionMonitor.extractSkipped')
        : t('evolutionMonitor.extractResult', {
            instincts: api.extractResult.data.extracted,
            clusters: api.extractResult.data.clusters,
          })
      : null;

  const extractBannerType = api.extractResult.error
    ? 'error'
    : api.extractResult.data && !api.extractResult.data.skipped
      ? 'success'
      : null;

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
            <button
              className="mcp-add-btn"
              onClick={handleExtract}
              disabled={api.extractResult.loading}
              type="button"
            >
              {api.extractResult.loading ? t('evolutionMonitor.extracting') : t('evolutionMonitor.extractNow')}
            </button>
            <h2>{t('evolutionMonitor.title')}</h2>
          </div>
        </div>
        {extractBannerType && (
          <div className={`mcp-feedback-banner mcp-feedback-banner--${extractBannerType}`}>
            {extractMsg}
          </div>
        )}
        <EvolutionDetail evolutionId={detailId} api={api} />
      </div>
    );
  }

  return (
    <div className="evolution-page detail-page">
      <div className="evolution-header skills-header detail-header">
        <button
          className="evolution-back-btn skills-back-btn detail-back-btn"
          onClick={() => navigate(-1)}
          type="button"
        >
          {t('common.back')}
        </button>
        <div className="evolution-header-title-group skills-header-title-group">
          <button
            className="mcp-add-btn"
            onClick={handleExtract}
            disabled={api.extractResult.loading}
            type="button"
          >
            {api.extractResult.loading ? t('evolutionMonitor.extracting') : t('evolutionMonitor.extractNow')}
          </button>
          <h2>{t('evolutionMonitor.title')}</h2>
        </div>
      </div>
      {extractBannerType && (
        <div className={`mcp-feedback-banner mcp-feedback-banner--${extractBannerType}`}>
          {extractMsg}
        </div>
      )}

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
          fetchSessionMessages={api.fetchSessionMessages}
        />
      )}
    </div>
  );
}

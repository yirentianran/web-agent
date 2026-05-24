import React from 'react';
import { useTranslation } from 'react-i18next';
import type { EvolutionStats } from '../../hooks/useEvolutionApi';

interface Props {
  stats: EvolutionStats | null;
  loading: boolean;
}

const CARD_CONFIG = [
  { key: 'today_events' as const, labelKey: 'evolutionMonitor.todayEvents' },
  { key: 'active_instincts' as const, labelKey: 'evolutionMonitor.activeInstincts' },
  { key: 'pending_reviews' as const, labelKey: 'evolutionMonitor.pendingReviews' },
  { key: 'week_auto_applied' as const, labelKey: 'evolutionMonitor.weekAutoApplied' },
];

export const StatsCards: React.FC<Props> = ({ stats, loading }) => {
  const { t } = useTranslation();
  return (
    <div className="stats-cards">
      {CARD_CONFIG.map(({ key, labelKey }) => (
        <div className="stats-card" key={key}>
          <div className="stats-card-body">
            <span className="stats-card-value">
              {loading ? '—' : stats?.[key] ?? 0}
            </span>
            <span className="stats-card-label">{t(labelKey)}</span>
          </div>
        </div>
      ))}
    </div>
  );
};

import React from 'react';
import type { EvolutionStats } from '../../hooks/useEvolutionApi';

interface Props {
  stats: EvolutionStats | null;
  loading: boolean;
}

const CARD_CONFIG = [
  { key: 'today_events' as const, icon: '📡', label: '今日事件' },
  { key: 'active_instincts' as const, icon: '🧬', label: '活跃本能' },
  { key: 'pending_reviews' as const, icon: '⏳', label: '待审核' },
  { key: 'week_auto_applied' as const, icon: '⚡', label: '本周自动应用' },
];

export const StatsCards: React.FC<Props> = ({ stats, loading }) => (
  <div className="stats-cards">
    {CARD_CONFIG.map(({ key, icon, label }) => (
      <div className="stats-card" key={key}>
        <span className="stats-card-icon">{icon}</span>
        <div className="stats-card-body">
          <span className="stats-card-value">
            {loading ? '—' : stats?.[key] ?? 0}
          </span>
          <span className="stats-card-label">{label}</span>
        </div>
      </div>
    ))}
  </div>
);

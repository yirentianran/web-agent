import React from 'react';
import { useTranslation } from 'react-i18next';
import type { EvolutionStats } from '../../hooks/useEvolutionApi';

interface Props {
  stats: EvolutionStats | null;
}

const WINDOW_LABELS: Record<string, string> = {
  today: 'evolutionMonitor.timeToday',
  last_7_days: 'evolutionMonitor.time7Days',
  last_30_days: 'evolutionMonitor.time30Days',
  last_90_days: 'evolutionMonitor.timeAll',
};

export const PipelineFunnel: React.FC<Props> = ({ stats }) => {
  const { t } = useTranslation();
  if (!stats) return null;
  const { funnel, time_window } = stats;
  const stages = [
    { labelKey: 'evolutionMonitor.observations', value: funnel.observations, key: 'observations' },
    { labelKey: 'evolutionMonitor.instincts', value: funnel.active_instincts, key: 'instincts' },
    { labelKey: 'evolutionMonitor.evolutionsStage', value: funnel.active_evolutions, key: 'evolutions' },
    { labelKey: 'evolutionMonitor.proposed', value: funnel.proposed_evolutions, key: 'proposed' },
  ];
  const maxVal = Math.max(...stages.map((s) => s.value), 1);

  return (
    <div className="pipeline-funnel">
      {stages.map(({ labelKey, value, key }, i) => (
        <React.Fragment key={key}>
          {i > 0 && <span className="funnel-arrow">→</span>}
          <div className="funnel-stage">
            <span className="funnel-label">{t(labelKey)}</span>
            <span className="funnel-value">{value}</span>
            <div className="funnel-bar">
              <div
                className="funnel-bar-fill"
                style={{ width: `${(value / maxVal) * 100}%` }}
              />
            </div>
          </div>
        </React.Fragment>
      ))}
      {time_window && (
        <p className="funnel-window-label">
          {t(WINDOW_LABELS[time_window] || time_window)}
        </p>
      )}
    </div>
  );
};

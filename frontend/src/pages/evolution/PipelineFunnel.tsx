import React from 'react';
import { useTranslation } from 'react-i18next';
import type { EvolutionStats } from '../../hooks/useEvolutionApi';

interface Props {
  stats: EvolutionStats | null;
}

export const PipelineFunnel: React.FC<Props> = ({ stats }) => {
  const { t } = useTranslation();
  if (!stats) return null;
  const { funnel } = stats;
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
    </div>
  );
};

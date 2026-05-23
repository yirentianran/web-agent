import React from 'react';
import type { EvolutionStats } from '../../hooks/useEvolutionApi';

interface Props {
  stats: EvolutionStats | null;
}

export const PipelineFunnel: React.FC<Props> = ({ stats }) => {
  if (!stats) return null;
  const { funnel } = stats;
  const stages = [
    { label: 'Observations', value: funnel.observations, key: 'observations' },
    { label: 'Instincts', value: funnel.active_instincts, key: 'instincts' },
    { label: 'Evolutions', value: funnel.active_evolutions, key: 'evolutions' },
    { label: 'Proposed', value: funnel.proposed_evolutions, key: 'proposed' },
  ];
  const maxVal = Math.max(...stages.map((s) => s.value), 1);

  return (
    <div className="pipeline-funnel">
      {stages.map(({ label, value, key }, i) => (
        <React.Fragment key={key}>
          {i > 0 && <span className="funnel-arrow">→</span>}
          <div className="funnel-stage">
            <span className="funnel-label">{label}</span>
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

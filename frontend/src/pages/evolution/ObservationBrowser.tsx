import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { ObservationItem } from '../../hooks/useEvolutionApi';

interface Props {
  data: { items: ObservationItem[]; total: number; page: number } | null;
  loading: boolean;
  error: string | null;
  onFilterChange: (filters: { session_id?: string; event_type?: string }) => void;
}

const EVENT_TYPES = [
  'tool_call_start', 'tool_call_end', 'user_correct',
  'user_retry', 'user_interrupt', 'session_complete', 'session_error',
];

export const ObservationBrowser: React.FC<Props> = ({ data, loading, error, onFilterChange }) => {
  const { t } = useTranslation();
  const [sessionId, setSessionId] = useState('');
  const [eventType, setEventType] = useState('');

  const handleFilter = () => onFilterChange(
    { session_id: sessionId || undefined, event_type: eventType || undefined }
  );

  return (
    <div>
      <div className="filter-bar">
        <input
          type="text" placeholder={t('evolutionMonitor.sessionId')}
          value={sessionId} onChange={(e) => setSessionId(e.target.value)}
        />
        <select value={eventType} onChange={(e) => setEventType(e.target.value)}>
          <option value="">{t('evolutionMonitor.allTypes')}</option>
          {EVENT_TYPES.map((et) => (
            <option key={et} value={et}>{et}</option>
          ))}
        </select>
        <button onClick={handleFilter} className="btn-filter">{t('evolutionMonitor.filter')}</button>
      </div>
      {loading && <div className="evo-loading">{t('evolutionMonitor.loadingObservations')}</div>}
      {error && <div className="evo-error">{error}</div>}
      {!loading && !error && (!data || data.items.length === 0) && (
        <div className="evo-empty">{t('evolutionMonitor.noObservations')}</div>
      )}
      {!loading && !error && data && data.items.length > 0 && (
        <>
          <table className="evo-table">
            <thead>
              <tr>
                <th>{t('evolutionMonitor.id')}</th>
                <th>{t('evolutionMonitor.session')}</th>
                <th>{t('evolutionMonitor.type')}</th>
                <th>{t('evolutionMonitor.tool')}</th>
                <th>{t('evolutionMonitor.success')}</th>
                <th>{t('evolutionMonitor.time')}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((obs) => (
                <tr key={obs.id} className="evo-row">
                  <td>{obs.id}</td>
                  <td>{obs.session_id.substring(0, 12)}...</td>
                  <td><span className="evo-badge">{obs.event_type}</span></td>
                  <td>{obs.tool_name || '—'}</td>
                  <td>{obs.success === null ? '—' : obs.success ? '✓' : '✗'}</td>
                  <td>{new Date(obs.created_at * 1000).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="evo-pagination">Total: {data.total}</div>
        </>
      )}
    </div>
  );
};

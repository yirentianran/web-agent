import React, { useState } from 'react';
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
  const [sessionId, setSessionId] = useState('');
  const [eventType, setEventType] = useState('');

  const handleFilter = () => onFilterChange(
    { session_id: sessionId || undefined, event_type: eventType || undefined }
  );

  if (loading) return <div className="evo-loading">Loading observations...</div>;
  if (error) return <div className="evo-error">{error}</div>;
  if (!data || data.items.length === 0) return <div className="evo-empty">No observations found</div>;

  return (
    <div>
      <div className="filter-bar">
        <input
          type="text" placeholder="Session ID"
          value={sessionId} onChange={(e) => setSessionId(e.target.value)}
        />
        <select value={eventType} onChange={(e) => setEventType(e.target.value)}>
          <option value="">All types</option>
          {EVENT_TYPES.map((et) => (
            <option key={et} value={et}>{et}</option>
          ))}
        </select>
        <button onClick={handleFilter} className="btn-filter">Filter</button>
      </div>
      <table className="evo-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Session</th>
            <th>Type</th>
            <th>Tool</th>
            <th>Success</th>
            <th>Time</th>
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
    </div>
  );
};

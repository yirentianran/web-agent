import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import type { ObservationItem, SessionMessage } from '../../hooks/useEvolutionApi';

interface Props {
  data: { items: ObservationItem[]; total: number; page: number } | null;
  loading: boolean;
  error: string | null;
  onFilterChange: (filters: { session_id?: string; event_type?: string }) => void;
  fetchSessionMessages: (sessionId: string) => Promise<SessionMessage[]>;
}

const EVENT_TYPES = [
  'tool_call_start', 'tool_call_end', 'user_correct',
  'user_retry', 'user_interrupt', 'session_complete', 'session_error',
];

function MessageBlock({ msg }: { msg: SessionMessage }) {
  const roleLabel =
    msg.type === 'user' ? 'User' :
    msg.type === 'assistant' ? 'Assistant' :
    msg.subtype === 'tool_use' ? 'Tool Use' :
    msg.subtype === 'tool_result' ? 'Tool Result' :
    msg.type === 'system' ? `System: ${msg.subtype || ''}` :
    `${msg.type}${msg.subtype ? `: ${msg.subtype}` : ''}`

  const body =
    msg.subtype === 'tool_use' && msg.input
      ? JSON.stringify(msg.input, null, 2)
      : msg.subtype === 'tool_result' && msg.result_content
        ? typeof msg.result_content === 'string'
          ? msg.result_content
          : JSON.stringify(msg.result_content, null, 2)
        : msg.content || (msg.subtype === 'tool_use' && msg.name ? `Call: ${msg.name}` : '')

  if (!body) return null

  return (
    <div className={`obs-msg ${msg.type}`}>
      <span className="obs-msg-role">{roleLabel}</span>
      <pre className="obs-msg-body">{body}</pre>
    </div>
  )
}

function ObsDetail({
  item,
  onBack,
  fetchSessionMessages,
}: {
  item: ObservationItem
  onBack: () => void
  fetchSessionMessages: (sessionId: string) => Promise<SessionMessage[]>
}) {
  const { t } = useTranslation();
  const [messages, setMessages] = useState<SessionMessage[]>([])
  const [msgsLoading, setMsgsLoading] = useState(false)

  useEffect(() => {
    setMsgsLoading(true)
    fetchSessionMessages(item.session_id)
      .then(setMessages)
      .catch(() => setMessages([]))
      .finally(() => setMsgsLoading(false))
  }, [item.session_id, fetchSessionMessages])

  return (
    <div className="obs-detail">
      <button className="evolution-back-btn skills-back-btn detail-back-btn" onClick={onBack} type="button">
        {t('evolutionMonitor.backToOverview')}
      </button>

      <h3 className="obs-detail-title">{t('evolutionMonitor.obsDetail')} #{item.id}</h3>

      <div className="obs-detail-grid">
        <div className="obs-detail-field">
          <span className="obs-detail-label">{t('evolutionMonitor.session')}</span>
          <span className="obs-detail-value">{item.session_id}</span>
        </div>
        <div className="obs-detail-field">
          <span className="obs-detail-label">{t('evolutionMonitor.type')}</span>
          <span className="evo-badge">{item.event_type}</span>
        </div>
        <div className="obs-detail-field">
          <span className="obs-detail-label">{t('evolutionMonitor.tool')}</span>
          <span className="obs-detail-value">{item.tool_name || '—'}</span>
        </div>
        <div className="obs-detail-field">
          <span className="obs-detail-label">{t('evolutionMonitor.success')}</span>
          <span className="obs-detail-value">
            {item.success === null ? '—' : item.success ? '✓' : '✗'}
          </span>
        </div>
        <div className="obs-detail-field">
          <span className="obs-detail-label">{t('evolutionMonitor.duration')}</span>
          <span className="obs-detail-value">{item.duration_ms}ms</span>
        </div>
        <div className="obs-detail-field">
          <span className="obs-detail-label">{t('evolutionMonitor.time')}</span>
          <span className="obs-detail-value">
            {new Date(item.created_at * 1000).toLocaleString()}
          </span>
        </div>
      </div>

      <div className="obs-detail-block">
        <h4>{t('evolutionMonitor.inputSummary')}</h4>
        <pre className="obs-detail-pre">
          {item.tool_input_summary || '—'}
        </pre>
      </div>

      <div className="obs-detail-block">
        <h4>{t('evolutionMonitor.outputSummary')}</h4>
        <pre className="obs-detail-pre">
          {item.tool_output_summary
            ? item.tool_output_summary
            : item.success === null ? '—' : item.success ? 'OK' : item.error_message || 'Error'}
        </pre>
      </div>

      {item.error_message && (
        <div className="obs-detail-block">
          <h4>{t('evolutionMonitor.errorMessage')}</h4>
          <pre className="obs-detail-pre obs-detail-error">{item.error_message}</pre>
        </div>
      )}

      <div className="obs-detail-block">
        <h4>{t('evolutionMonitor.sessionMessages')} ({messages.length})</h4>
        {msgsLoading && <div className="evo-loading">{t('common.loading')}</div>}
        {!msgsLoading && messages.length === 0 && (
          <div className="evo-empty" style={{ padding: '1rem' }}>{t('evolutionMonitor.noMessages')}</div>
        )}
        {messages.map((msg, i) => (
          <MessageBlock key={i} msg={msg} />
        ))}
      </div>
    </div>
  );
}

export const ObservationBrowser: React.FC<Props> = ({ data, loading, error, onFilterChange, fetchSessionMessages }) => {
  const { t } = useTranslation();
  const [sessionId, setSessionId] = useState('');
  const [eventType, setEventType] = useState('');
  const [selectedObs, setSelectedObs] = useState<ObservationItem | null>(null);

  const handleFilter = () => onFilterChange(
    { session_id: sessionId || undefined, event_type: eventType || undefined }
  );

  if (selectedObs) {
    return <ObsDetail item={selectedObs} onBack={() => setSelectedObs(null)} fetchSessionMessages={fetchSessionMessages} />;
  }

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
                <th>{t('evolutionMonitor.inputSummary')}</th>
                <th>{t('evolutionMonitor.outputSummary')}</th>
                <th>{t('evolutionMonitor.success')}</th>
                <th>{t('evolutionMonitor.time')}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((obs) => (
                <tr
                  key={obs.id}
                  className="evo-row"
                  onClick={() => setSelectedObs(obs)}
                >
                  <td>{obs.id}</td>
                  <td>{obs.session_id.substring(0, 12)}...</td>
                  <td><span className="evo-badge">{obs.event_type}</span></td>
                  <td>{obs.tool_name || '—'}</td>
                  <td className="cell-summary" title={obs.tool_input_summary}>
                    {obs.tool_input_summary
                      ? obs.tool_input_summary.length > 60
                        ? obs.tool_input_summary.substring(0, 60) + '...'
                        : obs.tool_input_summary
                      : '—'}
                  </td>
                  <td className="cell-summary" title={obs.tool_output_summary}>
                    {obs.tool_output_summary
                      ? obs.tool_output_summary.length > 60
                        ? obs.tool_output_summary.substring(0, 60) + '...'
                        : obs.tool_output_summary
                      : obs.success === null ? '—' : obs.success ? 'OK' : obs.error_message || 'Error'}
                  </td>
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

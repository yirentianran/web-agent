import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { InstinctItem } from '../../hooks/useEvolutionApi';

interface Props {
  data: { items: InstinctItem[]; total: number; page: number } | null;
  loading: boolean;
  error: string | null;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onFilterChange: (filters: { domain?: string; scope?: string }) => void;
}

const pageBtnStyle: React.CSSProperties = {
  padding: '4px 10px', border: '1px solid var(--color-border)',
  borderRadius: 4, background: 'var(--color-surface)', cursor: 'pointer',
}

export const InstinctList: React.FC<Props> = ({ data, loading, error, page, pageSize, onPageChange, onFilterChange }) => {
  const { t } = useTranslation();
  const [domain, setDomain] = useState('');
  const [scope, setScope] = useState('');

  const handleFilter = () => {
    onFilterChange(
      { domain: domain || undefined, scope: scope || undefined }
    );
    onPageChange(1);
  };

  return (
    <div>
      <div className="filter-bar">
        <select value={domain} onChange={(e) => setDomain(e.target.value)}>
          <option value="">{t('evolutionMonitor.allDomains')}</option>
          <option value="tool_usage">{t('evolutionMonitor.toolUsage')}</option>
          <option value="task_orchestration">{t('evolutionMonitor.taskOrchestration')}</option>
        </select>
        <select value={scope} onChange={(e) => setScope(e.target.value)}>
          <option value="">{t('evolutionMonitor.allScopes')}</option>
          <option value="active">{t('evolutionMonitor.active')}</option>
          <option value="deprecated">{t('evolutionMonitor.deprecated')}</option>
        </select>
        <button onClick={handleFilter} className="btn-filter">{t('evolutionMonitor.filter')}</button>
      </div>
      {loading && <div className="evo-loading">{t('evolutionMonitor.loadingInstincts')}</div>}
      {error && <div className="evo-error">{error}</div>}
      {!loading && !error && (!data || data.items.length === 0) && (
        <div className="evo-empty">{t('evolutionMonitor.noInstincts')}</div>
      )}
      {!loading && !error && data && data.items.length > 0 && (
        <>
          <table className="evo-table">
            <thead>
              <tr>
                <th>{t('evolutionMonitor.label')}</th>
                <th>{t('evolutionMonitor.trigger')}</th>
                <th>{t('evolutionMonitor.action')}</th>
                <th>{t('evolutionMonitor.confidence')}</th>
                <th>{t('evolutionMonitor.sources')}</th>
                <th>{t('evolutionMonitor.users')}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((inst) => (
                <tr key={inst.id} className="evo-row">
                  <td><span className="evo-badge">{inst.normalized_trigger}</span></td>
                  <td>{inst.trigger}</td>
                  <td>{inst.action}</td>
                  <td>{(inst.confidence * 100).toFixed(0)}%</td>
                  <td>{inst.source_count}</td>
                  <td>{inst.unique_user_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pagination page={page} total={data.total} pageSize={pageSize} onPageChange={onPageChange} />
        </>
      )}
    </div>
  );
};

function Pagination({
  page, total, pageSize, onPageChange,
}: {
  page: number; total: number; pageSize: number; onPageChange: (p: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  if (totalPages <= 1) return null;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'center', padding: 16, fontSize: '0.85rem' }}>
      <button disabled={page <= 1} onClick={() => onPageChange(page - 1)} style={pageBtnStyle}>←</button>
      {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
        const start = Math.max(1, Math.min(page - 2, totalPages - 4));
        const p = start + i;
        if (p > totalPages) return null;
        return (
          <button
            key={p}
            onClick={() => onPageChange(p)}
            style={{
              ...pageBtnStyle,
              ...(p === page ? { background: 'var(--color-primary)', color: '#fff', borderColor: 'var(--color-primary)' } : {}),
            }}
          >
            {p}
          </button>
        );
      })}
      <button disabled={page >= totalPages} onClick={() => onPageChange(page + 1)} style={pageBtnStyle}>→</button>
      <span style={{ color: 'var(--color-muted)', marginLeft: 12 }}>{total} total</span>
    </div>
  );
}

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { InstinctItem } from '../../hooks/useEvolutionApi';

interface Props {
  data: { items: InstinctItem[]; total: number; page: number } | null;
  loading: boolean;
  error: string | null;
  onFilterChange: (filters: { domain?: string; scope?: string }) => void;
}

export const InstinctList: React.FC<Props> = ({ data, loading, error, onFilterChange }) => {
  const { t } = useTranslation();
  const [domain, setDomain] = useState('');
  const [scope, setScope] = useState('');

  const handleFilter = () => onFilterChange(
    { domain: domain || undefined, scope: scope || undefined }
  );

  if (loading) return <div className="evo-loading">{t('evolutionMonitor.loadingInstincts')}</div>;
  if (error) return <div className="evo-error">{error}</div>;
  if (!data || data.items.length === 0) return <div className="evo-empty">{t('evolutionMonitor.noInstincts')}</div>;

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
      <div className="evo-pagination">Total: {data.total}</div>
    </div>
  );
};

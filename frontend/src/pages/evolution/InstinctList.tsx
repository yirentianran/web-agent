import React, { useState } from 'react';
import type { InstinctItem } from '../../hooks/useEvolutionApi';

interface Props {
  data: { items: InstinctItem[]; total: number; page: number } | null;
  loading: boolean;
  error: string | null;
  onFilterChange: (filters: { domain?: string; scope?: string }) => void;
}

export const InstinctList: React.FC<Props> = ({ data, loading, error, onFilterChange }) => {
  const [domain, setDomain] = useState('');
  const [scope, setScope] = useState('');

  const handleFilter = () => onFilterChange(
    { domain: domain || undefined, scope: scope || undefined }
  );

  if (loading) return <div className="evo-loading">Loading instincts...</div>;
  if (error) return <div className="evo-error">{error}</div>;
  if (!data || data.items.length === 0) return <div className="evo-empty">No instincts found</div>;

  return (
    <div>
      <div className="filter-bar">
        <select value={domain} onChange={(e) => setDomain(e.target.value)}>
          <option value="">All domains</option>
          <option value="tool_usage">Tool Usage</option>
          <option value="task_orchestration">Task Orchestration</option>
        </select>
        <select value={scope} onChange={(e) => setScope(e.target.value)}>
          <option value="">All scopes</option>
          <option value="active">Active</option>
          <option value="deprecated">Deprecated</option>
        </select>
        <button onClick={handleFilter} className="btn-filter">Filter</button>
      </div>
      <table className="evo-table">
        <thead>
          <tr>
            <th>Label</th>
            <th>Trigger</th>
            <th>Action</th>
            <th>Confidence</th>
            <th>Sources</th>
            <th>Users</th>
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

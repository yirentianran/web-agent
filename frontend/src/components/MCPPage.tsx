import { useState, useEffect, useCallback, type FormEvent } from 'react'
import { useMCPServers } from '../hooks/useMCPServers'
import type { McpServer, McpServerType } from '../lib/types'

interface MCPPageProps {
  userId: string
  authToken?: string | null
  onBack: () => void
}

interface ModalState {
  open: boolean
  mode: 'add' | 'edit'
  server: McpServer
  error: string
}

function emptyServer(): McpServer {
  return {
    name: '',
    type: 'stdio',
    command: '',
    args: [],
    url: '',
    env: {},
    tools: [],
    description: '',
    enabled: true,
  }
}

export default function MCPPage({ userId, authToken, onBack }: MCPPageProps) {
  const api = useMCPServers(authToken ?? null, userId)
  const [servers, setServers] = useState<McpServer[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [modal, setModal] = useState<ModalState>({ open: false, mode: 'add', server: emptyServer(), error: '' })
  const [actionLoading, setActionLoading] = useState<string | null>(null)

  const loadServers = useCallback(async () => {
    try {
      const data = await api.listServers()
      setServers(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load servers')
    } finally {
      setLoading(false)
    }
  }, [api])

  useEffect(() => { loadServers() }, [loadServers])

  const openAddModal = () => {
    setModal({ open: true, mode: 'add', server: emptyServer(), error: '' })
  }

  const openEditModal = (server: McpServer) => {
    setModal({ open: true, mode: 'edit', server: { ...server }, error: '' })
  }

  const closeModal = () => {
    setModal(prev => ({ ...prev, open: false }))
  }

  const handleSave = async (e: FormEvent) => {
    e.preventDefault()
    setActionLoading('save')
    setModal(prev => ({ ...prev, error: '' }))
    try {
      if (modal.mode === 'add') {
        await api.createServer(modal.server)
      } else {
        await api.updateServer(modal.server.name, modal.server)
      }
      closeModal()
      await loadServers()
    } catch (err) {
      setModal(prev => ({ ...prev, error: err instanceof Error ? err.message : 'Save failed' }))
    } finally {
      setActionLoading(null)
    }
  }

  const handleDelete = async (name: string) => {
    if (!confirm(`Delete MCP server "${name}"?`)) return
    setActionLoading(`delete-${name}`)
    try {
      await api.deleteServer(name)
      await loadServers()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    } finally {
      setActionLoading(null)
    }
  }

  const handleToggle = async (name: string, enabled: boolean) => {
    // Optimistic update
    setServers(prev => prev.map(s => s.name === name ? { ...s, enabled } : s))
    try {
      await api.toggleServer(name, enabled)
    } catch {
      // Rollback on failure
      await loadServers()
    }
  }

  const updateModalServer = (updates: Partial<McpServer>) => {
    setModal(prev => ({ ...prev, server: { ...prev.server, ...updates } }))
  }

  const addTool = () => {
    setModal(prev => ({ ...prev, server: { ...prev.server, tools: [...prev.server.tools, ''] } }))
  }

  const updateTool = (index: number, value: string) => {
    setModal(prev => ({
      ...prev,
      server: { ...prev.server, tools: prev.server.tools.map((t, i) => i === index ? value : t) }
    }))
  }

  const removeTool = (index: number) => {
    setModal(prev => ({ ...prev, server: { ...prev.server, tools: prev.server.tools.filter((_, i) => i !== index) } }))
  }

  if (loading) {
    return (
      <div className="mcp-page feedback-page">
        <div className="mcp-header feedback-header">
          <button className="mcp-back-btn feedback-back-btn" onClick={onBack} type="button">&larr; Back</button>
          <div className="mcp-header-title-group">
            <button className="mcp-add-btn" onClick={openAddModal} type="button">+ Add Server</button>
            <h2>MCP Servers</h2>
          </div>
        </div>
        <div className="mcp-loading">Loading...</div>
      </div>
    )
  }

  return (
    <div className="mcp-page feedback-page">
      <div className="mcp-header feedback-header">
        <button className="mcp-back-btn feedback-back-btn" onClick={onBack} type="button">&larr; Back</button>
        <div className="mcp-header-title-group">
          <button className="mcp-add-btn" onClick={openAddModal} type="button">+ Add Server</button>
          <h2>MCP Servers</h2>
        </div>
      </div>

      {error && <div className="mcp-error">{error}</div>}

      <div className="mcp-content">
        {servers.length === 0 ? (
          <div className="mcp-empty">
            <p>No MCP servers configured.</p>
          </div>
        ) : (
          <div className="mcp-list">
            {servers.map(server => (
              <div key={server.name} className={`mcp-card ${!server.enabled ? 'mcp-card--disabled' : ''}`}>
                <div className="mcp-card-header">
                  <div className="mcp-card-name">
                    <span className={`mcp-type-badge mcp-type-badge--${server.type}`}>
                      {server.type === 'stdio' ? '\u2318' : '\ud83c\udf10'} {server.name}
                    </span>
                    <span className="mcp-type-tag">{server.type}</span>
                  </div>
                  <div className="mcp-card-actions">
                    <label className="mcp-toggle">
                      <input
                        type="checkbox"
                        checked={server.enabled}
                        onChange={(e) => handleToggle(server.name, e.target.checked)}
                        disabled={actionLoading === `toggle-${server.name}`}
                      />
                      <span className="mcp-toggle-track" />
                    </label>
                    <button className="mcp-btn-edit" onClick={() => openEditModal(server)} type="button" title="Edit">
                      &#9998;
                    </button>
                    <button
                      className="mcp-btn-delete"
                      onClick={() => handleDelete(server.name)}
                      disabled={actionLoading === `delete-${server.name}`}
                      type="button"
                      title="Delete"
                    >
                      {actionLoading === `delete-${server.name}` ? '...' : '\u2715'}
                    </button>
                  </div>
                </div>
                {server.description && <p className="mcp-card-desc">{server.description}</p>}
                <div className="mcp-card-tools">
                  <strong>{server.tools.length}</strong> tool{server.tools.length !== 1 ? 's' : ''}: {server.tools.join(', ')}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Modal */}
      {modal.open && (
        <div className="mcp-modal-overlay" onClick={closeModal}>
          <div className="mcp-modal" onClick={(e) => e.stopPropagation()}>
            <div className="mcp-modal-header">
              <h2>{modal.mode === 'add' ? 'Add MCP Server' : 'Edit MCP Server'}</h2>
              <button className="mcp-modal-close" onClick={closeModal} type="button">&times;</button>
            </div>
            <form className="mcp-form" onSubmit={handleSave}>
              {modal.error && <div className="mcp-form-error">{modal.error}</div>}

              <div className="mcp-form-field">
                <label>Name *</label>
                <input
                  type="text"
                  value={modal.server.name}
                  onChange={(e) => updateModalServer({ name: e.target.value })}
                  required
                  placeholder="e.g. filesystem"
                />
              </div>

              <div className="mcp-form-field">
                <label>Type *</label>
                <div className="mcp-radio-group">
                  {(['stdio', 'http'] as McpServerType[]).map(type => (
                    <label key={type}>
                      <input
                        type="radio"
                        name="server-type"
                        checked={modal.server.type === type}
                        onChange={() => updateModalServer({ type })}
                      />
                      {type}
                    </label>
                  ))}
                </div>
              </div>

              {modal.server.type === 'stdio' ? (
                <>
                  <div className="mcp-form-field">
                    <label>Command *</label>
                    <input
                      type="text"
                      value={modal.server.command || ''}
                      onChange={(e) => updateModalServer({ command: e.target.value })}
                      required={modal.server.type === 'stdio'}
                      placeholder="e.g. npx, uv, python"
                    />
                  </div>
                  <div className="mcp-form-field">
                    <label>Args (one per line)</label>
                    <textarea
                      value={(modal.server.args || []).join('\n')}
                      onChange={(e) => updateModalServer({ args: e.target.value.split('\n').filter(Boolean) })}
                      rows={2}
                      placeholder="-y&#10;@modelcontextprotocol/server-fs&#10;/tmp"
                    />
                  </div>
                </>
              ) : (
                <div className="mcp-form-field">
                  <label>URL *</label>
                  <input
                    type="url"
                    value={modal.server.url || ''}
                    onChange={(e) => updateModalServer({ url: e.target.value })}
                    required={modal.server.type === 'http'}
                    placeholder="https://mcp.example.com/server"
                  />
                </div>
              )}

              <div className="mcp-form-field">
                <label>Description</label>
                <input
                  type="text"
                  value={modal.server.description}
                  onChange={(e) => updateModalServer({ description: e.target.value })}
                  placeholder="Optional description"
                />
              </div>

              <div className="mcp-form-field">
                <label>Tools *</label>
                {(modal.server.tools || []).map((tool, i) => (
                  <div key={i} className="mcp-tool-row">
                    <input
                      type="text"
                      value={tool}
                      onChange={(e) => updateTool(i, e.target.value)}
                      placeholder="tool name"
                      required
                    />
                    <button type="button" className="mcp-remove-btn" onClick={() => removeTool(i)}>&times;</button>
                  </div>
                ))}
                <button type="button" className="mcp-add-btn-sm" onClick={addTool}>+ Add Tool</button>
              </div>

              <div className="mcp-form-actions">
                <button type="button" className="mcp-btn-cancel" onClick={closeModal}>Cancel</button>
                <button type="submit" className="mcp-btn-save" disabled={actionLoading === 'save'}>
                  {actionLoading === 'save' ? 'Saving...' : 'Save'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}

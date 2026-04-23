import { useState, useEffect, useCallback, type FormEvent, useMemo } from 'react'
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
  jsonText: string
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

function serverToJson(server: McpServer): string {
  const { name, type, command, args, url, env, tools, description } = server
  const obj: Record<string, unknown> = { name, type }
  if (type === 'stdio') {
    if (command) obj.command = command
    if (args?.length) obj.args = args
  } else {
    if (url) obj.url = url
  }
  if (env && Object.keys(env).length > 0) obj.env = env
  if (tools.length > 0) obj.tools = tools
  if (description) obj.description = description
  return JSON.stringify(obj, null, 2)
}

function jsonToServer(text: string): McpServer {
  const parsed = JSON.parse(text)

  // Support MCP config format: {"mcpServers": {"name": {config...}}}
  if (parsed.mcpServers && typeof parsed.mcpServers === 'object') {
    const entries = Object.entries(parsed.mcpServers)
    if (entries.length === 0) {
      return emptyServer()
    }
    const [name, config] = entries[0]
    const srv = config as Record<string, unknown>
    return {
      name,
      type: ((srv.type as string) ?? 'stdio') as McpServerType,
      command: srv.command as string | undefined,
      args: Array.isArray(srv.args) ? srv.args as string[] : [],
      url: srv.url as string | undefined,
      env: (srv.env as Record<string, string>) ?? {},
      tools: Array.isArray(srv.tools) ? srv.tools as string[] : [],
      description: (srv.description as string) ?? '',
      enabled: true,
    }
  }

  // Single server format: {"name": "...", "type": "stdio", ...}
  return {
    name: parsed.name ?? '',
    type: (parsed.type ?? 'stdio') as McpServerType,
    command: parsed.command,
    args: parsed.args ?? [],
    url: parsed.url,
    env: parsed.env ?? {},
    tools: parsed.tools ?? [],
    description: parsed.description ?? '',
    enabled: true,
  }
}

function formatJsonText(text: string): string | null {
  try {
    const obj = JSON.parse(text)
    return JSON.stringify(obj, null, 2)
  } catch {
    return null
  }
}

function getPreviewText(text: string): string | null {
  try {
    const obj = JSON.parse(text)
    let target: Record<string, unknown> = obj

    // Unwrap MCP config format
    if (obj.mcpServers && typeof obj.mcpServers === 'object') {
      const entries = Object.entries(obj.mcpServers)
      if (entries.length > 0) {
        target = entries[0][1] as Record<string, unknown>
        if (!target.name) target = { name: entries[0][0], ...target }
      }
    }

    const parts: string[] = []
    if (target.name) parts.push(`name: ${target.name}`)
    if (target.type) parts.push(`type: ${target.type}`)
    if (target.command) parts.push(`command: ${target.command}`)
    else if (target.url) parts.push(`url: ${target.url}`)
    if (target.tools) parts.push(`${Array.isArray(target.tools) ? target.tools.length : 0} tools`)
    return parts.length > 0 ? parts.join(' | ') : null
  } catch {
    return null
  }
}

export default function MCPPage({ userId: _userId, authToken, onBack }: MCPPageProps) {
  const api = useMCPServers(authToken ?? null)
  const [servers, setServers] = useState<McpServer[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [modal, setModal] = useState<ModalState>({
    open: false,
    mode: 'add',
    server: emptyServer(),
    jsonText: '{}',
    error: '',
  })
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [saveFeedback, setSaveFeedback] = useState<{ status?: string; error?: string } | null>(null)
  const [serverStatuses, setServerStatuses] = useState<Record<string, { status?: string; error?: string }>>({})

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
    setModal({ open: true, mode: 'add', server: emptyServer(), jsonText: '{}', error: '' })
  }

  const openEditModal = (server: McpServer) => {
    setModal({ open: true, mode: 'edit', server: { ...server }, jsonText: serverToJson(server), error: '' })
  }

  const closeModal = () => {
    setModal(prev => ({ ...prev, open: false }))
  }

  const handleSave = async (e: FormEvent) => {
    e.preventDefault()
    setActionLoading('save')
    setSaveFeedback(null)

    // Parse and validate JSON
    let parsed: McpServer
    try {
      parsed = jsonToServer(modal.jsonText)
    } catch {
      setModal(prev => ({ ...prev, error: 'Invalid JSON' }))
      setActionLoading(null)
      return
    }

    if (!parsed.name) {
      setModal(prev => ({ ...prev, error: 'Missing required field: name' }))
      setActionLoading(null)
      return
    }

    try {
      const resp = modal.mode === 'add'
        ? await api.createServer(parsed)
        : await api.updateServer(modal.server.name, parsed)
      // Capture auto-discover feedback
      if (resp.discover_status === 'disconnected' && resp.discover_error) {
        setSaveFeedback({ error: resp.discover_error })
      } else if (resp.discover_status === 'connected') {
        setSaveFeedback({ status: 'Tools discovered successfully' })
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
    setServers(prev => prev.map(s => s.name === name ? { ...s, enabled } : s))
    try {
      await api.toggleServer(name, enabled)
    } catch {
      await loadServers()
    }
  }

  const handleReconnect = async (name: string) => {
    setActionLoading(`reconnect-${name}`)
    setServerStatuses(prev => ({ ...prev, [name]: { status: 'checking...', error: undefined } }))
    try {
      const result = await api.reconnectServer(name)
      setServerStatuses(prev => ({ ...prev, [name]: { status: result.status, error: result.error } }))
      await loadServers()
    } catch (err) {
      setServerStatuses(prev => ({
        ...prev,
        [name]: { error: err instanceof Error ? err.message : 'Reconnect failed' },
      }))
    } finally {
      setActionLoading(null)
    }
  }

  const handleDismissServerStatus = (name: string) => {
    setServerStatuses(prev => {
      const next = { ...prev }
      delete next[name]
      return next
    })
  }

  const handleFormat = () => {
    const formatted = formatJsonText(modal.jsonText)
    if (formatted) {
      setModal(prev => ({ ...prev, jsonText: formatted, error: '' }))
    }
  }

  const preview = useMemo(() => getPreviewText(modal.jsonText), [modal.jsonText])

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
      {saveFeedback?.error && (
        <div className="mcp-feedback-banner mcp-feedback-banner--error">
          Auto-discover failed: {saveFeedback.error}
          <button className="mcp-dismiss-btn" onClick={() => setSaveFeedback(null)} type="button">&times;</button>
        </div>
      )}
      {saveFeedback?.status && (
        <div className="mcp-feedback-banner mcp-feedback-banner--success">
          {saveFeedback.status}
          <button className="mcp-dismiss-btn" onClick={() => setSaveFeedback(null)} type="button">&times;</button>
        </div>
      )}

      <div className="mcp-content">
        {servers.length === 0 ? (
          <div className="mcp-empty">
            <p>No MCP servers configured.</p>
          </div>
        ) : (
          <div className="mcp-list">
            {servers.map(server => {
              const connStatus = serverStatuses[server.name]
              return (
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
                    <button
                      className="mcp-btn-reconnect"
                      onClick={() => handleReconnect(server.name)}
                      disabled={actionLoading === `reconnect-${server.name}`}
                      type="button"
                      title="Reconnect / Discover Tools"
                    >
                      {actionLoading === `reconnect-${server.name}` ? '...' : '\u21bb'}
                    </button>
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
                {connStatus && (
                  <div className={`mcp-conn-status mcp-conn-status--${connStatus.error ? 'error' : 'ok'}`}>
                    {connStatus.error
                      ? `Connection failed: ${connStatus.error}`
                      : `Connected — ${server.tools.length} tool${server.tools.length !== 1 ? 's' : ''} discovered`}
                    <button className="mcp-dismiss-btn" onClick={() => handleDismissServerStatus(server.name)} type="button">&times;</button>
                  </div>
                )}
              </div>
              )
            })}
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
                <label htmlFor="mcp-json-input">MCP Server Config (JSON) *</label>
                <textarea
                  id="mcp-json-input"
                  className="mcp-json-textarea"
                  value={modal.jsonText}
                  onChange={(e) => setModal(prev => ({ ...prev, jsonText: e.target.value, error: '' }))}
                  rows={16}
                  spellCheck={false}
                  placeholder={'{\n  "name": "mineru",\n  "type": "stdio",\n  "command": "uvx",\n  "args": ["mineru-mcp"],\n  "tools": ["parse_pdf"]\n}'}
                />
              </div>

              <div className="mcp-form-actions-secondary">
                <button type="button" className="mcp-btn-format" onClick={handleFormat}>Format</button>
                {preview && <span className="mcp-preview">{preview}</span>}
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

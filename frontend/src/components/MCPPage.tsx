import { useState, useEffect, useCallback, type FormEvent, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
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
    headers: {},
    env: {},
    tools: [],
    resources: [],
    prompts: [],
    description: '',
    enabled: true,
  }
}

function serverToJson(server: McpServer): string {
  const { name, type, command, args, url, headers, env, tools, resources, prompts, description } = server
  const obj: Record<string, unknown> = { name, type }
  if (type === 'stdio') {
    if (command) obj.command = command
    if (args?.length) obj.args = args
  } else {
    if (url) obj.url = url
    if (headers && Object.keys(headers).length > 0) obj.headers = headers
  }
  if (env && Object.keys(env).length > 0) obj.env = env
  if (tools.length > 0) obj.tools = tools
  if (resources.length > 0) obj.resources = resources
  if (prompts.length > 0) obj.prompts = prompts
  if (description) obj.description = description
  return JSON.stringify(obj, null, 2)
}

function inferType(srv: Record<string, unknown>): McpServerType {
  if (srv.url !== undefined && srv.url !== '') return 'streamable_http'
  if (srv.command !== undefined && srv.command !== '') return 'stdio'
  return 'stdio'
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
      type: (srv.type as McpServerType) ?? inferType(srv),
      command: srv.command as string | undefined,
      args: Array.isArray(srv.args) ? srv.args as string[] : [],
      url: srv.url as string | undefined,
      headers: (srv.headers as Record<string, string>) ?? {},
      env: (srv.env as Record<string, string>) ?? {},
      tools: Array.isArray(srv.tools) ? srv.tools as string[] : [],
      resources: Array.isArray(srv.resources) ? srv.resources : [],
      prompts: Array.isArray(srv.prompts) ? srv.prompts : [],
      description: (srv.description as string) ?? '',
      enabled: true,
    }
  }

  // Single server format: {"name": "...", "type": "stdio", ...}
  return {
    name: parsed.name ?? '',
    type: (parsed.type as McpServerType) ?? inferType(parsed as Record<string, unknown>),
    command: parsed.command,
    args: parsed.args ?? [],
    url: parsed.url,
    headers: parsed.headers ?? {},
    env: parsed.env ?? {},
    tools: parsed.tools ?? [],
    resources: parsed.resources ?? [],
    prompts: parsed.prompts ?? [],
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

function getPreviewLines(text: string): string[] | null {
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

    const line1: string[] = []
    const line2: string[] = []
    if (target.name) line1.push(`name: ${target.name}`)
    if (target.type) line1.push(`type: ${target.type}`)
    if (target.command) line2.push(`command: ${target.command}`)
    else if (target.url) line2.push(`url: ${target.url}`)
    if (target.tools) line2.push(`${Array.isArray(target.tools) ? target.tools.length : 0} tools`)
    if (target.resources) line2.push(`${Array.isArray(target.resources) ? target.resources.length : 0} resources`)
    if (target.prompts) line2.push(`${Array.isArray(target.prompts) ? target.prompts.length : 0} prompts`)

    const lines = [line1.join(' | '), line2.join(' | ')].filter(l => l)
    return lines.length > 0 ? lines : null
  } catch {
    return null
  }
}

export default function MCPPage({ userId: _userId, authToken: _authToken, onBack }: MCPPageProps) {
  const { t } = useTranslation()
  const api = useMCPServers()
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
      setError(err instanceof Error ? err.message : t('mcp.loadFailed'))
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
      setModal(prev => ({ ...prev, error: t('mcp.invalidJson') }))
      setActionLoading(null)
      return
    }

    if (!parsed.name) {
      setModal(prev => ({ ...prev, error: t('mcp.missingName') }))
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
        setSaveFeedback({ status: t('mcp.toolsDiscovered') })
      }
      closeModal()
      await loadServers()
    } catch (err) {
      setModal(prev => ({ ...prev, error: err instanceof Error ? err.message : t('mcp.saveFailed') }))
    } finally {
      setActionLoading(null)
    }
  }

  const handleDelete = async (name: string) => {
    if (!confirm(t('mcp.confirmDelete', { name }))) return
    setActionLoading(`delete-${name}`)
    try {
      await api.deleteServer(name)
      await loadServers()
    } catch (err) {
      setError(err instanceof Error ? err.message : t('mcp.deleteFailed'))
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
    setServerStatuses(prev => ({ ...prev, [name]: { status: t('mcp.checking'), error: undefined } }))
    try {
      const result = await api.reconnectServer(name)
      setServerStatuses(prev => ({ ...prev, [name]: { status: result.status, error: result.error } }))
      await loadServers()
    } catch (err) {
      setServerStatuses(prev => ({
        ...prev,
        [name]: { error: err instanceof Error ? err.message : t('mcp.reconnectFailed') },
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

  const previewLines = useMemo(() => getPreviewLines(modal.jsonText), [modal.jsonText])

  if (loading) {
    return (
      <div className="mcp-page detail-page">
        <div className="mcp-header detail-header">
          <button className="mcp-back-btn detail-back-btn" onClick={onBack} type="button">{t('common.back')}</button>
          <div className="mcp-header-title-group">
            <button className="mcp-add-btn" onClick={openAddModal} type="button">{t('mcp.addServer')}</button>
            <h2>{t('mcp.title')}</h2>
          </div>
        </div>
        <div className="mcp-loading">{t('common.loading')}</div>
      </div>
    )
  }

  return (
    <div className="mcp-page detail-page">
      <div className="mcp-header detail-header">
        <button className="mcp-back-btn detail-back-btn" onClick={onBack} type="button">{t('common.back')}</button>
        <div className="mcp-header-title-group">
          <button className="mcp-add-btn" onClick={openAddModal} type="button">{t('mcp.addServer')}</button>
          <h2>{t('mcp.title')}</h2>
        </div>
      </div>

      {error && <div className="mcp-error">{error}</div>}
      {saveFeedback?.error && (
        <div className="mcp-feedback-banner mcp-feedback-banner--error">
          {t('mcp.autoDiscoverFailed', { error: saveFeedback.error })}
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
            <p>{t('mcp.empty')}</p>
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
                      title={t('mcp.reconnectTitle')}
                    >
                      {actionLoading === `reconnect-${server.name}` ? '...' : '\u21bb'}
                    </button>
                    <button className="mcp-btn-edit" onClick={() => openEditModal(server)} type="button" title={t('common.edit')}>
                      &#9998;
                    </button>
                    <button
                      className="mcp-btn-delete"
                      onClick={() => handleDelete(server.name)}
                      disabled={actionLoading === `delete-${server.name}`}
                      type="button"
                      title={t('common.delete')}
                    >
                      {actionLoading === `delete-${server.name}` ? '...' : '\u2715'}
                    </button>
                  </div>
                </div>
                {server.description && <p className="mcp-card-desc">{server.description}</p>}
                <div className="mcp-card-tools">
                  <strong>{t('mcp.toolCount', { count: server.tools.length })}</strong>: {server.tools.join(', ')}
                </div>
                {server.resources.length > 0 && (
                  <div className="mcp-card-resources">
                    <strong>{t('mcp.resourceCount', { count: server.resources.length })}</strong>
                    <ul className="mcp-card-list">
                      {server.resources.map((r, i) => (
                        <li key={i}>
                          <span className="mcp-resource-uri">{r.uri}</span>
                          {r.name && <span className="mcp-resource-name"> — {r.name}</span>}
                          {r.mimeType && <span className="mcp-resource-mime"> [{r.mimeType}]</span>}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {server.prompts.length > 0 && (
                  <div className="mcp-card-prompts">
                    <strong>{t('mcp.promptCount', { count: server.prompts.length })}</strong>
                    <ul className="mcp-card-list">
                      {server.prompts.map((p, i) => (
                        <li key={i}>
                          <span className="mcp-prompt-name">{p.name}</span>
                          {p.description && <span className="mcp-prompt-desc"> — {p.description}</span>}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {connStatus && (
                  <div className={`mcp-conn-status mcp-conn-status--${connStatus.error ? 'error' : 'ok'}`}>
                    {connStatus.error
                      ? t('mcp.connectionFailed', { error: connStatus.error })
                      : t('mcp.connectedTools', { count: server.tools.length })}
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
              <h2>{modal.mode === 'add' ? t('mcp.addModalTitle') : t('mcp.editModalTitle')}</h2>
              <button className="mcp-modal-close" onClick={closeModal} type="button">&times;</button>
            </div>
            <form className="mcp-form" onSubmit={handleSave}>
              {modal.error && <div className="mcp-form-error">{modal.error}</div>}

              <div className="mcp-form-field">
                <label htmlFor="mcp-json-input">{t('mcp.configLabel')}</label>
                <textarea
                  id="mcp-json-input"
                  className="mcp-json-textarea"
                  value={modal.jsonText}
                  onChange={(e) => setModal(prev => ({ ...prev, jsonText: e.target.value, error: '' }))}
                  rows={16}
                  spellCheck={false}
                  placeholder={'{\n  "name": "myserver",\n  "type": "stdio",\n  "command": "uvx",\n  "args": ["my-mcp-package"],\n  "tools": ["list_files"]\n}'}
                />
              </div>

              <div className="mcp-form-actions-secondary">
                <button type="button" className="mcp-btn-format" onClick={handleFormat}>{t('common.format')}</button>
                {previewLines && <div className="mcp-preview">{previewLines.map((line, i) => <div key={i}>{line}</div>)}</div>}
              </div>

              <div className="mcp-form-actions">
                <button type="button" className="mcp-btn-cancel" onClick={closeModal}>{t('common.cancel')}</button>
                <button type="submit" className="mcp-btn-save" disabled={actionLoading === 'save'}>
                  {actionLoading === 'save' ? t('common.saving') : t('common.save')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}

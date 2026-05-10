import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'

interface FileInfo {
  filename: string
  path: string
  stored_name: string
  size: number
  source: 'upload' | 'generated'
  modified_at?: string
  download_url?: string
}

function formatTime(isoString: string | undefined): string {
  if (!isoString) return ''
  const date = new Date(isoString)
  if (isNaN(date.getTime())) return ''
  const now = new Date()
  const isToday = date.toDateString() === now.toDateString()
  if (isToday) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }
  return date.toLocaleDateString([], { month: '2-digit', day: '2-digit' }) + ' ' +
    date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

interface SessionFilePanelProps {
  userId: string
  authToken?: string | null
  activeSessionId: string | null
  onFileClick: (filename: string) => void
  refreshKey?: number
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1048576).toFixed(1)} MB`
}

export default function SessionFilePanel({
  userId,
  authToken,
  activeSessionId,
  onFileClick,
  refreshKey,
}: SessionFilePanelProps) {
  const { t } = useTranslation()
  const [scope, setScope] = useState<'all' | 'session'>('session')
  const [files, setFiles] = useState<FileInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [deleting, setDeleting] = useState<string | null>(null)

  const headers: Record<string, string> = {}
  if (authToken) headers['Authorization'] = `Bearer ${authToken}`

  const fetchFiles = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      let data: FileInfo[] = []
      if (scope === 'session' && activeSessionId) {
        const resp = await fetch(`/api/users/${userId}/sessions/${activeSessionId}/files`, { headers })
        if (resp.status === 403 || resp.status === 404) {
          window.location.href = window.location.origin
          return
        }
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const raw = await resp.json()
        data = raw.map((f: Record<string, unknown>) => {
            const apiSource = (f.source as 'upload' | 'generated') || 'upload'
            let fullPath = f.filename as string
            // For generated files without path prefix, prepend outputs/
            if (apiSource === 'generated' && !fullPath.startsWith('outputs/') && !fullPath.startsWith('uploads/')) {
              fullPath = `outputs/${fullPath}`
            }
            const displayName = fullPath.includes('/')
              ? fullPath.split('/').pop() || fullPath
              : fullPath
            return {
              filename: displayName,
              path: fullPath,
              stored_name: (f.stored_name as string) || fullPath,
              size: (f.size as number) || 0,
              source: apiSource,
              download_url: f.download_url as string | undefined,
              modified_at: f.generated_at as string | undefined,
            }
          })
      } else if (scope === 'all') {
        const resp = await fetch(`/api/users/${userId}/files`, { headers })
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const raw = await resp.json()
        data = raw.map((f: Record<string, unknown>) => {
            const apiSource = (f.source as 'upload' | 'generated') || 'upload'
            let fullPath = f.filename as string
            if (apiSource === 'generated' && !fullPath.startsWith('outputs/') && !fullPath.startsWith('uploads/')) {
              fullPath = `outputs/${fullPath}`
            }
            const displayName = fullPath.includes('/')
              ? fullPath.split('/').pop() || fullPath
              : fullPath
            return {
              filename: displayName,
              path: fullPath,
              stored_name: (f.stored_name as string) || fullPath,
              size: (f.size as number) || 0,
              source: apiSource,
              download_url: f.download_url as string | undefined,
              modified_at: f.generated_at as string | undefined,
            }
          })
      }
      // Sort by time descending (newest first)
      data.sort((a, b) => {
        if (!a.modified_at && !b.modified_at) return 0
        if (!a.modified_at) return 1
        if (!b.modified_at) return -1
        return new Date(b.modified_at).getTime() - new Date(a.modified_at).getTime()
      })
      setFiles(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : t('filePanel.loadFailed'))
    }
    setLoading(false)
  }, [userId, activeSessionId, scope, refreshKey])

  useEffect(() => {
    fetchFiles()
  }, [fetchFiles])

  const handleDelete = async (file: FileInfo) => {
    if (!confirm(t('filePanel.confirmDelete', { filename: file.filename }))) return
    setDeleting(file.stored_name)
    try {
      const resp = await fetch(`/api/users/${userId}/files/${encodeURIComponent(file.path)}`, {
        method: 'DELETE',
        headers,
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      await fetchFiles()
    } catch (err) {
      setError(err instanceof Error ? err.message : t('filePanel.deleteFailed'))
    }
    setDeleting(null)
  }

  const uploadFiles = files.filter(f => f.source === 'upload')
  const generatedFiles = files.filter(f => f.source === 'generated')

  return (
    <div className="session-file-panel">

      <div className="sfp-scope-tabs">
        <button
          className={`sfp-scope-tab ${scope === 'session' ? 'active' : ''}`}
          onClick={() => setScope('session')}
          type="button"
        >
          {t('filePanel.sessionScope')}
        </button>
        <button
          className={`sfp-scope-tab ${scope === 'all' ? 'active' : ''}`}
          onClick={() => setScope('all')}
          type="button"
        >
          {t('filePanel.allScope')}
        </button>
      </div>

      {error && <div className="sfp-error">{error}</div>}

      {loading ? (
        <div className="sfp-loading">{t('common.loading')}</div>
      ) : (
        <div className="sfp-groups">
          <FileGroup
            title={t('filePanel.uploadsGroup')}
            files={uploadFiles}
            userId={userId}
            authToken={authToken}
            onFileClick={onFileClick}
            onDelete={handleDelete}
            deleting={deleting}
          />
          <FileGroup
            title={t('filePanel.generatedGroup')}
            files={generatedFiles}
            userId={userId}
            authToken={authToken}
            onFileClick={onFileClick}
            onDelete={handleDelete}
            deleting={deleting}
          />
        </div>
      )}
    </div>
  )
}

function FileGroup({
  title,
  files,
  userId,
  authToken,
  onFileClick,
  onDelete,
  deleting,
}: {
  title: string
  files: FileInfo[]
  userId: string
  authToken?: string | null
  onFileClick: (filename: string) => void
  onDelete: (file: FileInfo) => void
  deleting: string | null
}) {
  const { t } = useTranslation()
  const [collapsed, setCollapsed] = useState(false)

  return (
    <div className="sfp-group">
      <button
        className="sfp-group-title"
        onClick={() => setCollapsed(v => !v)}
        type="button"
      >
        <span className={`sfp-group-arrow ${collapsed ? '' : 'open'}`}>&#9654;</span>
        {title} ({files.length})
      </button>
      {!collapsed && (
        <div className="sfp-group-items">
          {files.length === 0 ? (
            <div className="sfp-group-empty">{title === t('filePanel.uploadsGroup') ? t('filePanel.noUploads') : t('filePanel.noGenerated')}</div>
          ) : (
            files.map(f => (
              <div key={f.stored_name} className="sfp-item">
                <button
                  className="sfp-item-name"
                  onClick={() => onFileClick(f.filename)}
                  type="button"
                  title={t('filePanel.referenceFile', { filename: f.filename })}
                >
                  {f.filename}
                </button>
                <span className="sfp-item-size">{formatBytes(f.size)}</span>
                {f.modified_at && (
                  <span className="sfp-item-time" title={new Date(f.modified_at).toLocaleString()}>
                    {formatTime(f.modified_at)}
                  </span>
                )}
                <button
                  type="button"
                  className="sfp-item-dl"
                  onClick={async () => {
                    try {
                      const dlUrl = f.download_url || `/api/users/${userId}/download/${encodeURIComponent(f.path)}`
                      const fetchHeaders: Record<string, string> = {}
                      if (authToken) fetchHeaders['Authorization'] = `Bearer ${authToken}`
                      const response = await fetch(dlUrl, { headers: fetchHeaders })
                      if (!response.ok) throw new Error(`HTTP ${response.status}`)
                      const blob = await response.blob()
                      const url = window.URL.createObjectURL(blob)
                      const a = document.createElement('a')
                      a.href = url
                      a.download = f.filename
                      document.body.appendChild(a)
                      a.click()
                      document.body.removeChild(a)
                      window.URL.revokeObjectURL(url)
                    } catch (e) {
                      alert(e instanceof Error ? e.message : 'Download failed')
                    }
                  }}
                  title={t('common.download')}
                >
                  &#8595;
                </button>
                <button
                  className="sfp-item-del"
                  onClick={() => onDelete(f)}
                  disabled={deleting === f.stored_name}
                  type="button"
                  title={t('common.delete')}
                >
                  {deleting === f.stored_name ? '...' : '\u2715'}
                </button>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}

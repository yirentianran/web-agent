import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'

interface FileInfo {
  filename: string
  path: string
  size: number
  source: 'upload' | 'generated'
  modified_at?: number
  download_url?: string
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
            const fullPath = f.filename as string
            const displayName = fullPath.split('/').pop() || fullPath
            return {
              filename: displayName,
              path: fullPath,
              size: (f.size as number) || 0,
              source: fullPath.startsWith('outputs/') ? 'generated' : 'upload',
              download_url: f.download_url as string | undefined,
              modified_at: f.generated_at as number | undefined,
            }
          })
      } else if (scope === 'all') {
        const resp = await fetch(`/api/users/${userId}/files`, { headers })
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const raw = await resp.json()
        data = raw
          .filter((f: Record<string, unknown>) => {
            const name = (f.path || f.filename) as string
            return name.startsWith('uploads/') || name.startsWith('outputs/')
          })
          .map((f: Record<string, unknown>) => {
            const name = (f.path || f.filename) as string
            return {
              filename: name.split('/').pop() || name,
              path: name,
              size: (f.size as number) || 0,
              source: name.startsWith('outputs/') ? 'generated' : 'upload',
            }
          })
      }
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
    setDeleting(file.path)
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
              <div key={f.path} className="sfp-item">
                <button
                  className="sfp-item-name"
                  onClick={() => onFileClick(f.filename)}
                  type="button"
                  title={t('filePanel.referenceFile', { filename: f.filename })}
                >
                  {f.filename}
                </button>
                <span className="sfp-item-size">{formatBytes(f.size)}</span>
                <a
                  className="sfp-item-dl"
                  href={`/api/users/${userId}/download/${encodeURIComponent(f.path)}?token=${encodeURIComponent(authToken || '')}`}
                  download
                  title={t('common.download')}
                >
                  &#8595;
                </a>
                <button
                  className="sfp-item-del"
                  onClick={() => onDelete(f)}
                  disabled={deleting === f.path}
                  type="button"
                  title={t('common.delete')}
                >
                  {deleting === f.path ? '...' : '\u2715'}
                </button>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}

import { useTranslation } from 'react-i18next'

interface FileCardProps {
  filename: string
  size?: number
  downloadUrl?: string
  authToken?: string | null
  onRemove?: () => void
  onFileClick?: (filename: string) => void
  status?: 'uploaded' | 'result' | 'error'
}

async function downloadViaFetch(downloadUrl: string, basename: string, authToken: string | null): Promise<void> {
  const headers: Record<string, string> = {}
  if (authToken) headers['Authorization'] = `Bearer ${authToken}`

  const response = await fetch(downloadUrl, { headers })
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) {
      throw new Error('Session expired, please re-login')
    }
    if (response.status === 404) {
      throw new Error('File not found')
    }
    throw new Error(`Download failed (HTTP ${response.status})`)
  }

  const blob = await response.blob()
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = basename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  window.URL.revokeObjectURL(url)
}

function FileCard({ filename, size, downloadUrl, authToken, onRemove, onFileClick, status = 'uploaded' }: FileCardProps) {
  const { t } = useTranslation()
  // filename may be a full relative path (e.g. "outputs/reports/report.docx").
  // Extract just the basename for display and the download attribute.
  const basename = filename.includes('/') || filename.includes('\\')
    ? filename.replace(/\\/g, '/').split('/').pop()!
    : filename
  const ext = basename.includes('.') ? basename.split('.').pop()! : ''

  const handleDownload = async () => {
    try {
      await downloadViaFetch(downloadUrl!, basename, authToken ?? null)
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Download failed')
    }
  }

  return (
    <div className={`file-card file-card-${status}`}>
      <div className="file-card-icon" onClick={() => onFileClick?.(basename)} style={{ cursor: onFileClick ? 'pointer' : 'default' }}>
        <span className="file-ext">{ext}</span>
      </div>
      <div className="file-card-info" onClick={() => onFileClick?.(basename)} style={{ cursor: onFileClick ? 'pointer' : 'default' }}>
        <span className="file-card-name" title={filename}>{basename}</span>
        {size !== undefined && (
          <span className="file-card-size">{formatBytes(size)}</span>
        )}
      </div>
      <div className="file-card-actions">
        {downloadUrl && (
          <button
            type="button"
            className="file-card-download"
            onClick={handleDownload}
            aria-label={t('fileCards.downloadAria', { basename })}
          >
            &#11015;
          </button>
        )}
        {onRemove && (
          <button type="button" className="file-card-remove" onClick={onRemove} aria-label={t('fileCards.removeAria', { basename })}>
            &times;
          </button>
        )}
      </div>
    </div>
  )
}

interface FileCardListProps {
  files: Array<{ filename: string; size?: number; downloadUrl?: string }>
  authToken?: string | null
  onRemove?: (index: number) => void
  onFileClick?: (filename: string) => void
  status?: 'uploaded' | 'result' | 'error'
}

export function FileCardList({ files, authToken, onRemove, onFileClick, status }: FileCardListProps) {
  if (files.length === 0) return null

  // Deduplicate files by filename, keeping the last occurrence (which may have more complete data)
  const seen = new Map<string, typeof files[number]>()
  for (const f of files) {
    seen.set(f.filename, f)
  }
  const uniqueFiles = Array.from(seen.values())

  return (
    <div className="file-card-list">
      {uniqueFiles.map((f, i) => (
        <FileCard
          key={`${f.filename}-${i}`}
          filename={f.filename}
          size={f.size}
          downloadUrl={f.downloadUrl}
          authToken={authToken}
          onRemove={onRemove ? () => onRemove(i) : undefined}
          onFileClick={onFileClick}
          status={status}
        />
      ))}
    </div>
  )
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

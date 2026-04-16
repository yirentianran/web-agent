interface FileCardProps {
  filename: string
  size?: number
  downloadUrl?: string
  onRemove?: () => void
  onFileClick?: (filename: string) => void
  status?: 'uploaded' | 'result' | 'error'
}

export function FileCard({ filename, size, downloadUrl, onRemove, onFileClick, status = 'uploaded' }: FileCardProps) {
  const ext = filename.includes('.') ? filename.split('.').pop()! : ''
  return (
    <div className={`file-card file-card-${status}`}>
      <div className="file-card-icon" onClick={() => onFileClick?.(filename)} style={{ cursor: onFileClick ? 'pointer' : 'default' }}>
        <span className="file-ext">{ext}</span>
      </div>
      <div className="file-card-info" onClick={() => onFileClick?.(filename)} style={{ cursor: onFileClick ? 'pointer' : 'default' }}>
        <span className="file-card-name" title={filename}>{filename}</span>
        {size !== undefined && (
          <span className="file-card-size">{formatBytes(size)}</span>
        )}
      </div>
      <div className="file-card-actions">
        {downloadUrl && (
          <a href={downloadUrl} download={filename} className="file-card-download" aria-label={`Download ${filename}`}>
            &#11015;
          </a>
        )}
        {onRemove && (
          <button type="button" className="file-card-remove" onClick={onRemove} aria-label={`Remove ${filename}`}>
            &times;
          </button>
        )}
      </div>
    </div>
  )
}

interface FileCardListProps {
  files: Array<{ filename: string; size?: number; downloadUrl?: string }>
  onRemove?: (index: number) => void
  onFileClick?: (filename: string) => void
  status?: 'uploaded' | 'result' | 'error'
}

export function FileCardList({ files, onRemove, onFileClick, status }: FileCardListProps) {
  if (files.length === 0) return null
  return (
    <div className="file-card-list">
      {files.map((f, i) => (
        <FileCard
          key={f.filename + i}
          filename={f.filename}
          size={f.size}
          downloadUrl={f.downloadUrl}
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

import { useState, useEffect } from 'react'
import { FileCardList } from './FileCards'

interface FilesPanelProps {
  userId: string
  onClose: () => void
}

interface GeneratedFile {
  filename: string
  size: number
  generated_at: string
  download_url: string
}

export default function FilesPanel({ userId, onClose }: FilesPanelProps) {
  const [generatedFiles, setGeneratedFiles] = useState<GeneratedFile[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    fetch(`/api/users/${userId}/generated-files`)
      .then(r => r.json())
      .then(files => setGeneratedFiles(Array.isArray(files) ? files : []))
      .catch(() => setGeneratedFiles([]))
      .finally(() => setLoading(false))
  }, [userId])

  return (
    <div className="files-overlay" onClick={onClose}>
      <div className="files-panel-container" onClick={(e) => e.stopPropagation()}>
        <div className="files-panel">
          <div className="sp-tabs">
            <span className="sp-tab active">Files</span>
          </div>
          <div className="sp-tab-content">
          {loading ? (
            <div className="files-loading">Loading files...</div>
          ) : generatedFiles.length === 0 ? (
            <p className="files-empty">No generated files yet</p>
          ) : (
            <FileCardList
              files={generatedFiles.map(f => ({
                filename: f.filename,
                size: f.size,
                downloadUrl: f.download_url,
              }))}
              status="result"
            />
          )}
          </div>
          <button className="sp-close-btn" onClick={onClose} type="button" aria-label="Close files panel">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </div>
      </div>
    </div>
  )
}

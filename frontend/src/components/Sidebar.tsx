import type { SessionItem } from '../lib/types'

interface SidebarProps {
  sessions: SessionItem[]
  activeSession: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onDelete?: (id: string) => void
  onOpenFiles?: () => void
  filesCount?: number
}

export default function Sidebar({ sessions, activeSession, onSelect, onNew, onDelete, onOpenFiles, filesCount }: SidebarProps) {
  return (
    <aside className="sidebar">
      <button className="btn-new-session" onClick={onNew}>
        + New Session
      </button>
      <div className="sidebar-list">
        {sessions.length === 0 && (
          <p className="sidebar-empty">No sessions yet</p>
        )}
        {sessions.map((session) => (
          <div
            key={session.session_id}
            className={`sidebar-item ${activeSession === session.session_id ? 'active' : ''}`}
            onClick={() => onSelect(session.session_id)}
          >
            <span className="session-dot">{activeSession === session.session_id ? '●' : '○'}</span>
            <span className="session-title">{session.title || session.session_id.slice(0, 20)}</span>
            {onDelete && (
              <button
                className="btn-delete-session"
                onClick={(e) => { e.stopPropagation(); onDelete(session.session_id) }}
                aria-label="Delete"
              >
                ✕
              </button>
            )}
          </div>
        ))}
      </div>
      <div className="sidebar-footer">
        {onOpenFiles && (
          <button className="btn-open-files" onClick={onOpenFiles} type="button">
            <span className="sp-files-icon">📁</span>
            <span className="sp-files-label">Files</span>
            {filesCount !== undefined && filesCount > 0 && (
              <span className="files-count">{filesCount}</span>
            )}
          </button>
        )}
      </div>
    </aside>
  )
}
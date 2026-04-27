import { useState, useRef, useEffect } from 'react'
import type { SessionItem } from '../lib/types'

interface SidebarProps {
  sessions: SessionItem[]
  activeSession: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onDelete?: (id: string) => void
  onRename?: (id: string, title: string) => void
  onOpenFiles?: () => void
  filesCount?: number
}

export default function Sidebar({ sessions, activeSession, onSelect, onNew, onDelete, onRename, onOpenFiles, filesCount }: SidebarProps) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const editRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editingId && editRef.current) {
      editRef.current.focus()
      editRef.current.select()
    }
  }, [editingId])

  const handleDoubleClick = (session: SessionItem) => {
    if (onRename) {
      setEditingId(session.session_id)
    }
  }

  const commitRename = (sessionId: string, value: string) => {
    const trimmed = value.trim()
    if (trimmed && onRename) {
      onRename(sessionId, trimmed)
    }
    setEditingId(null)
  }

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
            {editingId === session.session_id ? (
              <input
                ref={editRef}
                className="session-title-input"
                defaultValue={session.title || session.session_id.slice(0, 20)}
                onBlur={(e) => commitRename(session.session_id, e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') commitRename(session.session_id, e.currentTarget.value)
                  if (e.key === 'Escape') setEditingId(null)
                }}
                onClick={(e) => e.stopPropagation()}
              />
            ) : (
              <span
                className="session-title"
                onDoubleClick={() => handleDoubleClick(session)}
                title="Double-click to rename"
              >
                {session.title || session.session_id.slice(0, 20)}
              </span>
            )}
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
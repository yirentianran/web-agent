import { useState, useRef, useEffect, type MouseEvent } from 'react'
import { useTranslation } from 'react-i18next'

interface SettingsMenuProps {
  onOpenSkills: () => void
  onOpenEvolution: () => void
  onOpenMCP: () => void
  onOpenDashboard: () => void
  onOpenUsers: () => void
  onOpenSessions: () => void
  userRole: string
}

export default function SettingsMenu({ onOpenSkills, onOpenEvolution, onOpenMCP, onOpenDashboard, onOpenUsers, onOpenSessions, userRole }: SettingsMenuProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent | globalThis.MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler as EventListener)
    return () => document.removeEventListener('mousedown', handler as EventListener)
  }, [open])

  const handleAction = (action: () => void) => {
    action()
    setOpen(false)
  }

  const isAdmin = userRole === "admin";

  return (
    <div className="settings-menu" ref={ref}>
      <button className="settings-menu-trigger" onClick={() => setOpen(v => !v)} type="button" aria-expanded={open} aria-haspopup="menu" title={t('header.settings')}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      </button>
      {open && (
        <div className="settings-menu-dropdown" role="menu">
          {isAdmin && (
            <button className="settings-menu-item" role="menuitem" onClick={() => handleAction(onOpenDashboard)} type="button">
              <span className="settings-menu-item-icon">📊</span>
              {t('header.usageDashboard')}
            </button>
          )}
          {isAdmin && (
            <button className="settings-menu-item" role="menuitem" onClick={() => handleAction(onOpenSessions)} type="button">
              <span className="settings-menu-item-icon">📋</span>
              {t('sessions.title')}
            </button>
          )}
          <button className="settings-menu-item" role="menuitem" onClick={() => handleAction(onOpenSkills)} type="button">
            <span className="settings-menu-item-icon">🧩</span>
            {t('header.skillsManagement')}
          </button>
          {isAdmin && (
            <button className="settings-menu-item" role="menuitem" onClick={() => handleAction(onOpenMCP)} type="button">
              <span className="settings-menu-item-icon">⚡</span>
              {t('header.mcpServers')}
            </button>
          )}
          {isAdmin && (
            <button className="settings-menu-item" role="menuitem" onClick={() => handleAction(onOpenUsers)} type="button">
              <span className="settings-menu-item-icon">👥</span>
              {t('header.users')}
            </button>
          )}
          {isAdmin && (
            <button className="settings-menu-item" role="menuitem" onClick={() => handleAction(onOpenEvolution)} type="button">
              <span className="settings-menu-item-icon">🧬</span>
              {t('header.skillEvolution')}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

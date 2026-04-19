import { useState, useRef, useEffect, type MouseEvent } from 'react'

interface UserMenuProps {
  userId: string
  onOpenSettings: () => void
  onOpenFeedback: () => void
  onOpenEvolution: () => void
  onOpenMCP: () => void
  onLogout: () => void
}

export default function UserMenu({ userId, onOpenSettings, onOpenFeedback, onOpenEvolution, onOpenMCP, onLogout }: UserMenuProps) {
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

  const toggle = () => setOpen((v) => !v)

  const handleAction = (action: () => void) => {
    action()
    setOpen(false)
  }

  return (
    <div className="user-menu" ref={ref}>
      <button className="user-menu-trigger" onClick={toggle} type="button" aria-expanded={open} aria-haspopup="menu">
        <span className="user-menu-avatar">👤</span>
        <span className="user-menu-name">{userId}</span>
        <svg className="user-menu-chevron" width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 4.5 6 7.5 9 4.5" />
        </svg>
      </button>
      {open && (
        <div className="user-menu-dropdown" role="menu">
          <button className="user-menu-item" role="menuitem" onClick={() => handleAction(onOpenSettings)} type="button">
            <span className="user-menu-item-icon">⚙</span>
            Skills Management
          </button>
          <button className="user-menu-item" role="menuitem" onClick={() => handleAction(onOpenMCP)} type="button">
            <span className="user-menu-item-icon">⚡</span>
            MCP Servers
          </button>
          <button className="user-menu-item" role="menuitem" onClick={() => handleAction(onOpenFeedback)} type="button">
            <span className="user-menu-item-icon">💬</span>
            Feedback Management
          </button>
          <button className="user-menu-item" role="menuitem" onClick={() => handleAction(onOpenEvolution)} type="button">
            <span className="user-menu-item-icon">🧬</span>
            Skill Evolution
          </button>
          <button className="user-menu-item user-menu-item--logout" role="menuitem" onClick={() => handleAction(onLogout)} type="button">
            <span className="user-menu-item-icon">⏻</span>
            Logout
          </button>
        </div>
      )}
    </div>
  )
}

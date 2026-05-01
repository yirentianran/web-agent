import { useState, useRef, useEffect, type MouseEvent } from 'react'
import { useTranslation } from 'react-i18next'

interface UserMenuProps {
  userId: string
  onLogout: () => void
}

export default function UserMenu({ userId, onLogout }: UserMenuProps) {
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

  const toggle = () => setOpen((v) => !v)

  const handleLogout = () => {
    onLogout()
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
          <button className="user-menu-item user-menu-item--logout" role="menuitem" onClick={handleLogout} type="button">
            <span className="user-menu-item-icon">⏻</span>
            {t('header.logout')}
          </button>
        </div>
      )}
    </div>
  )
}
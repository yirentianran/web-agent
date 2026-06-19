import { useTranslation } from 'react-i18next'
import UserMenu from './UserMenu'
import SettingsMenu from './SettingsMenu'
import LanguageSwitcher from '../i18n/LanguageSwitcher'
import ThemeToggle from './ThemeToggle'
import type { ConnectionStatus } from '../lib/types'

interface HeaderProps {
  connectionStatus: ConnectionStatus
  userId: string
  authToken?: string | null
  onOpenSkills: () => void
  onOpenEvolution: () => void
  onOpenMCP: () => void
  onOpenDashboard: () => void
  onOpenUsers: () => void
  onOpenSessions: () => void
  onLogout: () => void
  userRole: string
}

export default function Header({ connectionStatus, userId, authToken, onOpenSkills, onOpenEvolution, onOpenMCP, onOpenDashboard, onOpenUsers, onOpenSessions, onLogout, userRole }: HeaderProps) {
  const { t } = useTranslation()

  const statusKey: Record<ConnectionStatus, string> = {
    connected: 'connection.connected',
    connecting: 'connection.connecting',
    reconnecting: 'connection.reconnecting',
    recovered: 'connection.recovered',
    expired: 'connection.expired',
    failed: 'connection.failed',
  }

  return (
    <header className="app-header">
      <div className="app-brand">
        <span className="app-logo">◎</span>
        <span className="app-name">{t('header.brandName')}</span>
      </div>
      <div className="app-header-actions">
        <div className="app-connection">
          <span className={`app-status-dot ${connectionStatus === 'connected' || connectionStatus === 'recovered' ? 'connected' : connectionStatus === 'failed' || connectionStatus === 'expired' ? 'failed' : 'reconnecting'}`} />
          <span className="app-status-text">{t(statusKey[connectionStatus])}</span>
        </div>
        <LanguageSwitcher userId={userId} authToken={authToken} />
        <ThemeToggle />
        <SettingsMenu
          onOpenSkills={onOpenSkills}

          onOpenEvolution={onOpenEvolution}
          onOpenMCP={onOpenMCP}
          onOpenDashboard={onOpenDashboard}
          onOpenUsers={onOpenUsers}
          onOpenSessions={onOpenSessions}
          userRole={userRole}
        />
        <UserMenu userId={userId} onLogout={onLogout} />
      </div>
    </header>
  )
}

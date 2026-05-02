import { useTranslation } from 'react-i18next'
import UserMenu from './UserMenu'
import SettingsMenu from './SettingsMenu'
import LanguageSwitcher from '../i18n/LanguageSwitcher'
import ThemeToggle from './ThemeToggle'
import type { ConnectionStatus } from '../lib/types'

interface HeaderProps {
  connectionStatus: ConnectionStatus
  userId: string
  onOpenSkills: () => void
  onOpenFeedback: () => void
  onOpenEvolution: () => void
  onOpenMCP: () => void
  onOpenMemory: () => void
  onLogout: () => void
}

export default function Header({ connectionStatus, userId, onOpenSkills, onOpenFeedback, onOpenEvolution, onOpenMCP, onOpenMemory, onLogout }: HeaderProps) {
  const { t } = useTranslation()

  const statusKey: Record<ConnectionStatus, string> = {
    connected: 'connection.connected',
    connecting: 'connection.connecting',
    reconnecting: 'connection.reconnecting',
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
          <span className={`app-status-dot ${connectionStatus === 'connected' ? 'connected' : connectionStatus === 'failed' ? 'failed' : 'reconnecting'}`} />
          <span className="app-status-text">{t(statusKey[connectionStatus])}</span>
        </div>
        <LanguageSwitcher />
        <ThemeToggle />
        <SettingsMenu
          onOpenSkills={onOpenSkills}
          onOpenFeedback={onOpenFeedback}
          onOpenEvolution={onOpenEvolution}
          onOpenMCP={onOpenMCP}
          onOpenMemory={onOpenMemory}
        />
        <UserMenu userId={userId} onLogout={onLogout} />
      </div>
    </header>
  )
}
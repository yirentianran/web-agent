import UserMenu from './UserMenu'

interface HeaderProps {
  connected: boolean
  userId: string
  onOpenSettings: () => void
  onOpenFeedback: () => void
  onLogout: () => void
}

export default function Header({ connected, userId, onOpenSettings, onOpenFeedback, onLogout }: HeaderProps) {
  return (
    <header className="app-header">
      <div className="app-brand">
        <span className="app-logo">◎</span>
        <span className="app-name">Web Agent</span>
      </div>
      <div className="app-header-actions">
        <div className="app-connection">
          <span className={`app-status-dot ${connected ? 'connected' : ''}`} />
          <span className="app-status-text">{connected ? 'Connected' : 'Disconnected'}</span>
        </div>
        <UserMenu userId={userId} onOpenSettings={onOpenSettings} onOpenFeedback={onOpenFeedback} onLogout={onLogout} />
      </div>
    </header>
  )
}
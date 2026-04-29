import UserMenu from './UserMenu'
import type { ConnectionStatus } from '../lib/types'

interface HeaderProps {
  connectionStatus: ConnectionStatus
  userId: string
  onOpenSkills: () => void
  onOpenFeedback: () => void
  onOpenEvolution: () => void
  onOpenMCP: () => void
  onLogout: () => void
}

const STATUS_LABELS: Record<ConnectionStatus, string> = {
  connected: 'Connected',
  connecting: 'Connecting...',
  reconnecting: 'Reconnecting...',
  failed: 'Disconnected',
}

export default function Header({ connectionStatus, userId, onOpenSkills, onOpenFeedback, onOpenEvolution, onOpenMCP, onLogout }: HeaderProps) {
  return (
    <header className="app-header">
      <div className="app-brand">
        <span className="app-logo">◎</span>
        <span className="app-name">Web Agent</span>
      </div>
      <div className="app-header-actions">
        <div className="app-connection">
          <span className={`app-status-dot ${connectionStatus === 'connected' ? 'connected' : connectionStatus === 'failed' ? 'failed' : 'reconnecting'}`} />
          <span className="app-status-text">{STATUS_LABELS[connectionStatus]}</span>
        </div>
        <UserMenu userId={userId} onOpenSkills={onOpenSkills} onOpenFeedback={onOpenFeedback} onOpenEvolution={onOpenEvolution} onOpenMCP={onOpenMCP} onLogout={onLogout} />
      </div>
    </header>
  )
}
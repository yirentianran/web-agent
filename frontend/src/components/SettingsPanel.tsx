import SkillsPanel from './SkillsPanel'

interface SettingsPanelProps {
  authToken: string | null
  userId: string
  onClose: () => void
}

export default function SettingsPanel({ authToken, userId, onClose }: SettingsPanelProps) {
  return (
    <div className="settings-overlay" onClick={onClose}>
      <div className="settings-panel-container" onClick={(e) => e.stopPropagation()}>
        <div className="settings-panel">
          <div className="sp-tabs">
            <button className="sp-tab active" disabled>
              Skills Management
            </button>
          </div>
          <div className="sp-tab-content">
            <SkillsPanel authToken={authToken} userId={userId} onClose={onClose} embedded />
          </div>
          <button className="sp-close-btn" onClick={onClose} type="button" aria-label="Close settings">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </div>
      </div>
    </div>
  )
}

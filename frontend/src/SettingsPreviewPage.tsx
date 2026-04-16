import SettingsPreview from './components/SettingsPreview'
import './components/SettingsPreview.css'

export default function SettingsPreviewPage() {
  return (
    <div className="settings-preview-page">
      {/* Preview */}
      <SettingsPreview theme="light" />

      {/* Back to App */}
      <a href="/" className="back-link">
        ← Back to Web Agent
      </a>
    </div>
  )
}
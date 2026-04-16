import TechPreview from './components/TechPreview'
import './components/TechPreview.css'

export default function TechPreviewPage() {
  return (
    <div className="tech-preview-page">
      <TechPreview />

      {/* Back to App */}
      <a href="/" className="tech-back-link">
        ← Back to Web Agent
      </a>
    </div>
  )
}
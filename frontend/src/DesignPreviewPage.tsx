import { useState } from 'react'
import DesignPreview from './components/DesignPreview'
import './components/DesignPreview.css'

export default function DesignPreviewPage() {
  const [style, setStyle] = useState<'tech' | 'modern'>('tech')

  return (
    <div className="design-preview-page">
      {/* Style Switcher */}
      <div className="style-switcher">
        <button
          className={`style-btn ${style === 'tech' ? 'active' : ''}`}
          onClick={() => setStyle('tech')}
        >
          <span className="style-icon">◎</span>
          <span className="style-label">Clean Tech</span>
          <span className="style-desc">Developer-centric, dark mode</span>
        </button>
        <button
          className={`style-btn ${style === 'modern' ? 'active' : ''}`}
          onClick={() => setStyle('modern')}
        >
          <span className="style-icon">✨</span>
          <span className="style-label">Modern Friendly</span>
          <span className="style-desc">Minimal, light mode</span>
        </button>
      </div>

      {/* Preview */}
      <DesignPreview style={style} />

      {/* Back to App */}
      <a href="/" className="back-link">
        ← Back to Web Agent
      </a>
    </div>
  )
}
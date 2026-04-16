import { useState } from 'react'
import './TechPreview.css'

// Mock skill data
const mockSkills = [
  { name: 'code-analyzer', version: '1.2.0', status: 'active' },
  { name: 'debug-assist', version: '0.8.5', status: 'active' },
  { name: 'refactor-bot', version: '2.0.1', status: 'paused' },
]

// Mock file data
const mockFiles = [
  { filename: 'neural_weights.json', size: '2.4 MB' },
  { filename: 'training_log.csv', size: '156 KB' },
  { filename: 'model_output.md', size: '12 KB' },
]

export default function TechPreview() {
  const [showSettings, setShowSettings] = useState(true)

  return (
    <div className="tech-preview">
      {/* Grid Background */}
      <div className="tech-grid-bg" />

      {/* Scanline Effect */}
      <div className="tech-scanline" />

      {/* Header */}
      <header className="tech-header">
        <div className="tech-brand">
          <span className="tech-logo">◈</span>
          <span className="tech-name">NEXUS AI</span>
          <span className="tech-tag">v2.4.1</span>
        </div>
        <div className="tech-header-actions">
          <div className="tech-status">
            <span className="tech-pulse" />
            <span className="tech-status-text">ONLINE</span>
          </div>
          <button className="tech-btn ghost" onClick={() => setShowSettings(true)}>
            <span className="tech-btn-icon">⚙</span>
            <span>CONFIG</span>
          </button>
          <button className="tech-btn ghost">
            <span className="tech-btn-icon">👤</span>
            <span>USER</span>
          </button>
        </div>
      </header>

      {/* Main Layout */}
      <div className="tech-layout">
        {/* Sidebar */}
        <aside className="tech-sidebar">
          <button className="tech-btn primary pulse">
            <span className="tech-btn-icon">+</span>
            <span>NEW SESSION</span>
          </button>

          <div className="tech-sessions">
            <div className="tech-session active">
              <span className="tech-node active" />
              <div className="tech-session-info">
                <span className="tech-session-title">Neural Network Optimization</span>
                <span className="tech-session-time">Running • 2.3s</span>
              </div>
              <span className="tech-session-badge">●</span>
            </div>
            <div className="tech-session">
              <span className="tech-node" />
              <div className="tech-session-info">
                <span className="tech-session-title">Code Refactor Module</span>
                <span className="tech-session-time">Completed</span>
              </div>
            </div>
            <div className="tech-session">
              <span className="tech-node" />
              <div className="tech-session-info">
                <span className="tech-session-title">Debug Analysis</span>
                <span className="tech-session-time">Paused</span>
              </div>
            </div>
          </div>

          {/* Sidebar Footer - Files */}
          <div className="tech-sidebar-footer">
            <button className="tech-btn outline files-btn">
              <span className="tech-btn-icon">◈</span>
              <span>DATA FILES</span>
              <span className="tech-count">{mockFiles.length}</span>
            </button>
          </div>
        </aside>

        {/* Main Content */}
        <main className="tech-main">
          {/* Welcome Screen */}
          <div className="tech-welcome">
            <div className="tech-welcome-glow" />
            <div className="tech-welcome-logo">◈</div>
            <h1 className="tech-welcome-title">
              NEXUS <span className="tech-highlight">AI</span> PLATFORM
            </h1>
            <p className="tech-welcome-desc">Advanced Neural Agent Interface</p>

            {/* Neural Network Visualization */}
            <div className="tech-neural-net">
              <div className="tech-node-visual" />
              <div className="tech-node-visual" />
              <div className="tech-node-visual active" />
              <div className="tech-node-visual" />
              <div className="tech-node-visual" />
              <div className="tech-connection" />
              <div className="tech-connection active" />
              <div className="tech-connection" />
            </div>
          </div>

          {/* Input Bar */}
          <div className="tech-input-bar">
            {/* Attach Button */}
            <button className="tech-btn attach-btn">
              <span className="tech-btn-icon">◈</span>
            </button>

            {/* Input Box */}
            <div className="tech-input-box">
              <div className="tech-input-glow" />
              <textarea
                className="tech-input-field"
                placeholder="Initialize neural command... [Shift+Enter for multi-line]"
                rows={3}
              />
              <div className="tech-input-indicator">
                <span className="tech-input-label">READY</span>
                <span className="tech-input-bar" />
              </div>
            </div>

            {/* Send Button */}
            <button className="tech-btn send-btn">
              <span className="tech-btn-icon">→</span>
            </button>
          </div>
        </main>

        {/* Settings Panel */}
        {showSettings && (
          <div className="tech-settings-overlay" onClick={() => setShowSettings(false)}>
            <div className="tech-settings-panel" onClick={(e) => e.stopPropagation()}>
              <div className="tech-panel-header">
                <span className="tech-panel-title">◈ SKILL MODULES</span>
                <button className="tech-btn close-btn" onClick={() => setShowSettings(false)}>×</button>
              </div>

              {/* Upload */}
              <div className="tech-upload-zone">
                <button className="tech-btn primary outline">
                  <span className="tech-btn-icon">◈</span>
                  <span>UPLOAD MODULE (.zip)</span>
                </button>
              </div>

              {/* Skill List */}
              <div className="tech-skill-list">
                {mockSkills.map((skill, i) => (
                  <div key={i} className="tech-skill-card">
                    <div className="tech-skill-header">
                      <span className="tech-skill-icon">◈</span>
                      <span className="tech-skill-name">{skill.name}</span>
                      <span className={`tech-skill-status ${skill.status}`}>
                        {skill.status}
                      </span>
                    </div>
                    <div className="tech-skill-meta">
                      Version: {skill.version}
                    </div>
                    <div className="tech-skill-actions">
                      <button className="tech-btn ghost small">VIEW</button>
                      <button className="tech-btn ghost small">DISABLE</button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="tech-footer">
        <span className="tech-footer-text">NEXUS AI • Neural Agent Interface • v2.4.1</span>
      </div>
    </div>
  )
}
import { useState } from 'react'
import './SettingsPreview.css'

interface SettingsPreviewProps {
  theme: 'light' | 'dark'
}

// Mock skill data
const mockSkills = [
  { name: 'test-skill', createdAt: '2026-04-12', source: 'personal' },
  { name: 'code-review', createdAt: '2026-04-10', source: 'personal' },
  { name: 'tdd-workflow', createdAt: '2026-04-08', source: 'personal' },
  { name: 'debug-helper', createdAt: '2026-04-05', source: 'shared' },
]

// Mock file data for sidebar
const mockFiles = [
  { filename: 'output.csv', size: 12345, createdAt: '2026-04-14', session: 'Refactor auth' },
  { filename: 'report.md', size: 45200, createdAt: '2026-04-14', session: 'Refactor auth' },
  { filename: 'auth_refactor.py', size: 8900, createdAt: '2026-04-12', session: 'Debug API' },
  { filename: 'test_auth.py', size: 3200, createdAt: '2026-04-12', session: 'Debug API' },
  { filename: 'api_fix.md', size: 2100, createdAt: '2026-04-10', session: 'Create skill' },
]

// Mock attached files in input
const mockAttachedFiles = [
  { name: 'requirements.pdf' },
  { name: 'config.json' },
]

function getFileIcon(filename: string): string {
  const ext = filename.split('.').pop() || ''
  const icons: Record<string, string> = {
    py: '🐍',
    md: '📝',
    csv: '📊',
    json: '📋',
    txt: '📄',
    pdf: '📕',
    zip: '📦',
  }
  return icons[ext] || '📄'
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export default function SettingsPreview({ theme }: SettingsPreviewProps) {
  const [showSettings, setShowSettings] = useState(false)
  const [showFiles, setShowFiles] = useState(false)

  return (
    <div className={`settings-preview settings-preview--${theme}`}>
      {/* Header */}
      <header className="sp-header">
        <div className="sp-brand">◎ Web Agent</div>
        <div className="sp-header-actions">
          {/* Connection Status */}
          <div className="sp-connection">
            <span className="sp-status-dot connected" />
            <span className="sp-status-text">Connected</span>
          </div>
          <button className="sp-settings-btn" onClick={() => setShowSettings(true)}>
            ⚙ Settings
          </button>
          <button className="sp-user-btn">👤 User</button>
        </div>
      </header>

      {/* Main Layout */}
      <div className="sp-layout">
        {/* Sidebar */}
        <aside className="sp-sidebar">
          <button className="sp-new-session">+ New Session</button>
          <div className="sp-session-list">
            <div className="sp-session active">● Refactor auth module</div>
            <div className="sp-session">○ Debug API endpoint</div>
            <div className="sp-session">○ Create skill</div>
          </div>

          {/* Sidebar Footer - Files */}
          <div className="sp-sidebar-footer">
            <button className="sp-files-btn" onClick={() => setShowFiles(true)}>
              <span className="sp-files-icon">📁</span>
              <span className="sp-files-label">Files</span>
              <span className="sp-files-count">{mockFiles.length}</span>
            </button>
          </div>
        </aside>

        {/* Main Content */}
        <main className="sp-main">
          <div className="sp-welcome">
            <div className="sp-welcome-logo">◎</div>
            <h1 className="sp-welcome-title">Web Agent</h1>
            <p className="sp-welcome-desc">Your AI-powered coding companion</p>
          </div>

          {/* Input Bar - Higher, with files inside */}
          <div className="sp-input-bar">
            {/* Attach Button - Left */}
            <button className="sp-attach-btn">
              <span className="sp-attach-icon">📎</span>
            </button>

            {/* Input Box - Center */}
            <div className="sp-input-box">
              {/* Attached Files Inside Input */}
              {mockAttachedFiles.length > 0 && (
                <div className="sp-attached-files">
                  {mockAttachedFiles.map((file, i) => (
                    <span key={i} className="sp-file-chip">
                      📎 {file.name}
                      <button className="sp-chip-remove">×</button>
                    </span>
                  ))}
                </div>
              )}
              <textarea
                className="sp-input-field"
                placeholder="Type your message... (Shift+Enter for newline)"
                rows={3}
              />
            </div>

            {/* Send Button - Right */}
            <button className="sp-send-btn">
              <span className="sp-send-icon">→</span>
            </button>
          </div>
        </main>

        {/* Settings Panel - Only Skills */}
        {showSettings && (
          <div className="sp-settings-overlay" onClick={() => setShowSettings(false)}>
            <div className="sp-settings-panel" onClick={(e) => e.stopPropagation()}>
              <button className="sp-close-btn" onClick={() => setShowSettings(false)}>✕</button>
              <div className="sp-panel-title">Settings — Skills</div>
              <div className="sp-tab-content">
                <div className="sp-skills-tab">
                  <div className="sp-upload-area">
                    <button className="sp-upload-btn">📦 Upload Skill (ZIP)</button>
                  </div>
                  <div className="sp-skill-tabs">
                    <button className="sp-skill-tab active">Personal</button>
                    <button className="sp-skill-tab">Shared</button>
                  </div>
                  <div className="sp-skill-list">
                    {mockSkills.map((skill, i) => (
                      <div key={i} className="sp-skill-row">
                        <div className="sp-skill-header">
                          <span className="sp-skill-icon">📦</span>
                          <span className="sp-skill-name">{skill.name}</span>
                          <span className={`sp-skill-badge ${skill.source}`}>{skill.source}</span>
                        </div>
                        <div className="sp-skill-meta">Created: {skill.createdAt}</div>
                        <div className="sp-skill-actions">
                          <button className="sp-skill-view">View</button>
                          <button className="sp-skill-delete">Delete</button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Files Panel */}
        {showFiles && (
          <div className="sp-files-overlay" onClick={() => setShowFiles(false)}>
            <div className="sp-files-panel" onClick={(e) => e.stopPropagation()}>
              <button className="sp-close-btn" onClick={() => setShowFiles(false)}>✕</button>
              <div className="sp-panel-title">Generated Files</div>

              {/* Search & Filter */}
              <div className="sp-file-search">
                <input type="text" placeholder="🔍 Search files..." className="sp-search-input" />
                <select className="sp-filter-select">
                  <option>All Types</option>
                  <option>.py</option>
                  <option>.md</option>
                  <option>.csv</option>
                </select>
                <select className="sp-sort-select">
                  <option>Date ↓</option>
                  <option>Date ↑</option>
                  <option>Name</option>
                  <option>Size</option>
                </select>
              </div>

              {/* File List */}
              <div className="sp-file-list">
                {mockFiles.map((file, i) => (
                  <div key={i} className="sp-file-row">
                    <div className="sp-file-header">
                      <input type="checkbox" className="sp-file-checkbox" />
                      <span className="sp-file-icon">{getFileIcon(file.filename)}</span>
                      <span className="sp-file-name">{file.filename}</span>
                      <button className="sp-download-btn">⬇</button>
                    </div>
                    <div className="sp-file-meta">
                      {formatSize(file.size)} • {file.createdAt} • {file.session}
                    </div>
                  </div>
                ))}
              </div>

              {/* Actions */}
              <div className="sp-download-actions">
                <button className="sp-action-btn">⬇ Download Selected</button>
                <button className="sp-action-btn primary">⬇ Download All (ZIP)</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
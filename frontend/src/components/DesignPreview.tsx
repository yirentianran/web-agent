import './DesignPreview.css'

interface DesignPreviewProps {
  style: 'tech' | 'modern'
}

export default function DesignPreview({ style }: DesignPreviewProps) {
  return (
    <div className={`design-preview design-preview--${style}`}>
      {/* Header */}
      <header className="dp-header">
        <div className="dp-brand">
          {style === 'tech' ? '◎ Web Agent' : 'Web Agent ✨'}
        </div>
        <div className="dp-header-actions">
          <button className="dp-theme-toggle">
            {style === 'tech' ? '☀ Light' : '🌙 Dark'}
          </button>
        </div>
      </header>

      {/* Main Layout */}
      <div className="dp-layout">
        {/* Sidebar */}
        <aside className="dp-sidebar">
          <button className="dp-new-session">
            <span>+ New Session</span>
          </button>
          <div className="dp-sessions">
            <div className="dp-session dp-session--active">
              <span className="dp-session-dot active"></span>
              <div className="dp-session-info">
                <span className="dp-session-title">Refactor auth module</span>
                <span className="dp-session-time">2 min ago</span>
              </div>
            </div>
            <div className="dp-session">
              <span className="dp-session-dot"></span>
              <div className="dp-session-info">
                <span className="dp-session-title">Debug API endpoint</span>
                <span className="dp-session-time">Yesterday</span>
              </div>
            </div>
            <div className="dp-session">
              <span className="dp-session-dot"></span>
              <div className="dp-session-info">
                <span className="dp-session-title">Create skill template</span>
                <span className="dp-session-time">3 days ago</span>
              </div>
            </div>
          </div>
          <div className="dp-sidebar-footer">
            <button className="dp-skills-btn">
              <span>⚙ Skills</span>
            </button>
          </div>
        </aside>

        {/* Chat Area */}
        <main className="dp-main">
          {/* Welcome Screen */}
          <div className="dp-welcome">
            <div className="dp-welcome-logo">
              {style === 'tech' ? '◎' : '✨'}
            </div>
            <h1 className="dp-welcome-title">
              {style === 'tech' ? 'Web Agent Platform' : 'Your AI Assistant'}
            </h1>
            <p className="dp-welcome-desc">
              {style === 'tech'
                ? 'Your AI-powered coding companion'
                : 'Ask anything, build everything'}
            </p>

            {/* Quick Start Cards */}
            <div className="dp-quick-start">
              <h3 className="dp-quick-start-label">
                {style === 'tech' ? '✨ Quick Start' : 'Try these prompts'}
              </h3>
              <div className="dp-quick-cards">
                {style === 'tech' ? (
                  <>
                    <button className="dp-quick-card">
                      <span className="dp-quick-icon">🔍</span>
                      <span className="dp-quick-text">Analyze codebase structure</span>
                    </button>
                    <button className="dp-quick-card">
                      <span className="dp-quick-icon">✏️</span>
                      <span className="dp-quick-text">Create new feature</span>
                    </button>
                    <button className="dp-quick-card">
                      <span className="dp-quick-icon">🐛</span>
                      <span className="dp-quick-text">Debug an issue</span>
                    </button>
                    <button className="dp-quick-card">
                      <span className="dp-quick-icon">♻️</span>
                      <span className="dp-quick-text">Refactor code</span>
                    </button>
                  </>
                ) : (
                  <>
                    <button className="dp-quick-card dp-quick-card--large">
                      <span className="dp-quick-icon">💬</span>
                      <span className="dp-quick-title">Chat with me</span>
                      <span className="dp-quick-desc">Ask questions about your code</span>
                    </button>
                    <button className="dp-quick-card dp-quick-card--large">
                      <span className="dp-quick-icon">🚀</span>
                      <span className="dp-quick-title">Build something</span>
                      <span className="dp-quick-desc">Create features, apps, tools</span>
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* Message Examples */}
          <div className="dp-messages">
            {/* User Message */}
            <div className="dp-message dp-message--user">
              <div className="dp-bubble">
                Help me refactor the authentication module for better security
              </div>
            </div>

            {/* Tool Message */}
            <div className="dp-message dp-message--tool">
              <div className="dp-tool-card">
                <span className="dp-tool-icon">📖</span>
                <span className="dp-tool-name">Read</span>
                <span className="dp-tool-detail">src/auth.ts</span>
              </div>
            </div>

            {/* Hook Spinner */}
            <div className="dp-message dp-message--system">
              <div className={`dp-spinner dp-spinner--${style}`}>
                <span className="dp-spinner-dot"></span>
                <span className="dp-spinner-dot"></span>
                <span className="dp-spinner-dot"></span>
                <span className="dp-spinner-text">Running hook: startup...</span>
              </div>
            </div>

            {/* Assistant Message */}
            <div className="dp-message dp-message--assistant">
              <div className="dp-bubble">
                <details className="dp-thinking">
                  <summary>◇ Thinking</summary>
                  <div className="dp-thinking-content">
                    The user wants to refactor the auth module. I should analyze the current structure first...
                  </div>
                </details>
                <p>I'll analyze the authentication module and propose security improvements.</p>
                <pre className="dp-code">
                  <code>// Current structure
├── src/auth/
│   ├── login.ts
│   ├── token.ts
│   └── middleware.ts</code>
                </pre>
              </div>
            </div>
          </div>

          {/* Input Bar */}
          <div className="dp-input-bar">
            <button className="dp-attach-btn">📎</button>
            <div className="dp-input-wrapper">
              <input
                type="text"
                className="dp-input"
                placeholder={style === 'tech' ? "Type your request..." : "What would you like to do?"}
              />
            </div>
            <button className={`dp-send-btn dp-send-btn--${style}`}>
              {style === 'tech' ? 'Send →' : 'Send ✨'}
            </button>
          </div>

          {/* Status Bar */}
          <div className="dp-status-bar">
            <span className="dp-status-dot connected"></span>
            <span>Connected</span>
            <span className="dp-session-id">Session: a422ec9b...</span>
            <span className="dp-cost">$0.0124</span>
          </div>
        </main>
      </div>

      {/* Style Label */}
      <div className="dp-style-label">
        Style: <strong>{style === 'tech' ? 'Clean Tech / Developer-Centric' : 'Modern Minimal / Friendly'}</strong>
      </div>
    </div>
  )
}
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

interface ErrorAction {
  label: string
  kind: string
}

interface ErrorCardProps {
  message: string
  severity?: 'critical' | 'retryable' | 'actionable'
  detail?: string
  actions?: ErrorAction[]
  /** When true, the card appears muted as a resolved/past error */
  isResolved?: boolean
  /** Called when user clicks an action button */
  onAction?: (kind: string) => void
}

const SEVERITY_STYLES: Record<string, { banner: string; icon: string }> = {
  critical: { banner: 'error-card--critical', icon: '🔴' },
  retryable: { banner: 'error-card--retryable', icon: '🟡' },
  actionable: { banner: 'error-card--actionable', icon: '🔵' },
}

export default function ErrorCard({
  message,
  severity = 'retryable',
  detail,
  actions,
  isResolved,
  onAction,
}: ErrorCardProps) {
  const { t } = useTranslation()
  const [showDetail, setShowDetail] = useState(false)
  const style = SEVERITY_STYLES[severity] || SEVERITY_STYLES.retryable

  return (
    <div
      className={`message error-card ${style.banner}${isResolved ? ' error-card--resolved' : ''}`}
    >
      <div className="error-card__header">
        <span className="error-card__icon">{style.icon}</span>
        <span className="error-card__message">{message}</span>
        {isResolved && (
          <span className="error-resolved-badge">{t('message.past')}</span>
        )}
      </div>

      {actions && actions.length > 0 && !isResolved && (
        <div className="error-card__actions">
          {actions.map((action) => (
            <button
              key={action.kind}
              className="error-card__action-btn"
              onClick={() => onAction?.(action.kind)}
            >
              {action.label}
            </button>
          ))}
        </div>
      )}

      {detail && (
        <div className="error-card__detail">
          <button
            className="error-card__detail-toggle"
            onClick={() => setShowDetail(!showDetail)}
          >
            {showDetail ? t('message.hideDetails') : t('message.showDetails')}
          </button>
          {showDetail && (
            <pre className="error-card__detail-text">{detail}</pre>
          )}
        </div>
      )}
    </div>
  )
}

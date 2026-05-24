import { useTranslation } from 'react-i18next'

interface ConfirmDialogProps {
  title: string
  userName: string
  confirmLabel: string
  confirmClass: 'danger' | 'primary'
  messageKey:
    | 'users.confirmDisableBody'
    | 'users.confirmEnableBody'
    | 'users.confirmPromoteBody'
    | 'users.confirmDemoteBody'
  loading: boolean
  onConfirm: () => void
  onCancel: () => void
}

export default function ConfirmDialog({
  title,
  userName,
  confirmLabel,
  confirmClass,
  messageKey,
  loading,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const { t } = useTranslation()

  const message = t(messageKey)
  const parts = message.split('{{user}}')

  return (
    <div
      style={{
        background: 'var(--color-surface, #fff)',
        border: '1px solid var(--color-border, #e2e8f0)',
        borderRadius: '12px',
        padding: '20px',
        marginTop: '20px',
      }}
    >
      <h3
        style={{
          fontSize: '0.9375rem',
          fontWeight: 600,
          color: 'var(--color-text, #1a202c)',
          margin: '0 0 16px 0',
        }}
      >
        {title}
      </h3>
      <div
        style={{
          padding: '16px',
          background: 'var(--color-surface, #fff)',
          border:
            confirmClass === 'danger'
              ? '1px solid #fee2e2'
              : '1px solid var(--color-border, #e2e8f0)',
          borderRadius: '8px',
        }}
      >
        <p
          style={{ fontSize: '13px', color: 'var(--color-text-muted, #718096)', marginBottom: '12px' }}
        >
          {parts.length === 2 ? (
            <>
              {parts[0]}
              <strong>{userName}</strong>
              {parts[1]}
            </>
          ) : (
            message.replace('{{user}}', userName)
          )}
        </p>
        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
          <button
            onClick={onCancel}
            disabled={loading}
            style={{
              padding: '6px 14px',
              border: '1px solid var(--color-border, #e2e8f0)',
              borderRadius: '6px',
              background: 'var(--color-surface, #fff)',
              fontSize: '13px',
              cursor: 'pointer',
              color: 'var(--color-text-muted, #64748b)',
            }}
          >
            {t('users.confirmCancel')}
          </button>
          <button
            onClick={onConfirm}
            disabled={loading}
            style={{
              padding: '6px 14px',
              border: 'none',
              borderRadius: '6px',
              background: confirmClass === 'danger' ? '#dc2626' : '#3b82f6',
              color: '#fff',
              fontSize: '13px',
              cursor: 'pointer',
              opacity: loading ? 0.7 : 1,
            }}
          >
            {loading ? '…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

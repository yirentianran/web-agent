import { useTranslation } from 'react-i18next'
import type { UserItem } from '../../hooks/useUsersApi'

function formatTokens(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K'
  return String(n)
}

function formatDate(ts: number | null): string {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function formatShortDate(ts: number | null): string {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

interface UsersTableProps {
  items: UserItem[]
  currentUserId: string
  loading: boolean
  onDisable: (userId: string) => void
  onEnable: (userId: string) => void
  onPromote: (userId: string) => void
  onDemote: (userId: string) => void
}

export default function UsersTable({
  items,
  currentUserId,
  loading,
  onDisable,
  onEnable,
  onPromote,
  onDemote,
}: UsersTableProps) {
  const { t } = useTranslation()

  return (
    <div className="ranking-panel" style={{ marginTop: '20px' }}>
      <h3 className="ranking-title">{t('users.userList')}</h3>
      <table className="ranking-table">
        <thead>
          <tr>
            <th>{t('users.colUserId')}</th>
            <th>{t('users.colRole')}</th>
            <th>{t('users.colStatus')}</th>
            <th className="right">{t('users.colTokens')}</th>
            <th className="right">{t('users.colSessions')}</th>
            <th>{t('users.colRegistered')}</th>
            <th>{t('users.colLastActive')}</th>
            <th style={{ textAlign: 'center' }}>{t('users.colActions')}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((u) => {
            const isCurrentUser = u.user_id === currentUserId
            const isDisabled = u.status === 'disabled'
            return (
              <tr
                key={u.user_id}
                style={isDisabled ? { background: '#fef2f2' } : undefined}
              >
                <td style={{ fontWeight: 500 }}>{u.user_id}</td>
                <td>
                  <span
                    style={{
                      background: u.role === 'admin' ? '#dbeafe' : '#f1f5f9',
                      color: u.role === 'admin' ? '#3b82f6' : '#64748b',
                      padding: '1px 8px',
                      borderRadius: '4px',
                      fontSize: '12px',
                    }}
                  >
                    {u.role === 'admin' ? t('users.roleAdmin') : t('users.roleUser')}
                  </span>
                </td>
                <td>
                  <span
                    style={{
                      background: isDisabled ? '#fee2e2' : '#dcfce7',
                      color: isDisabled ? '#dc2626' : '#15803d',
                      padding: '1px 8px',
                      borderRadius: '4px',
                      fontSize: '12px',
                    }}
                  >
                    {isDisabled ? t('users.statusDisabled') : t('users.statusActive')}
                  </span>
                </td>
                <td className="right mono">{formatTokens(u.total_tokens)}</td>
                <td className="right">{u.session_count}</td>
                <td style={{ color: 'var(--color-text-muted, #718096)' }}>
                  {formatShortDate(u.created_at)}
                </td>
                <td style={{ color: 'var(--color-text-muted, #718096)' }}>
                  {formatDate(u.last_active_at)}
                </td>
                <td style={{ textAlign: 'center', whiteSpace: 'nowrap' }}>
                  {isCurrentUser ? (
                    <span style={{ fontSize: '12px', color: 'var(--color-text-muted, #718096)' }}>
                      {t('users.currentUser')}
                    </span>
                  ) : isDisabled ? (
                    <>
                      {u.disabled_by && u.disabled_at ? (
                        <span
                          style={{
                            fontSize: '11px',
                            color: 'var(--color-text-muted, #718096)',
                            display: 'block',
                          }}
                        >
                          {t('users.disabledBy', {
                            admin: u.disabled_by,
                            date: formatShortDate(u.disabled_at),
                          })}
                        </span>
                      ) : null}
                      <button
                        onClick={() => onEnable(u.user_id)}
                        disabled={loading}
                        style={{
                          padding: '4px 10px',
                          border: '1px solid var(--color-border, #e2e8f0)',
                          borderRadius: '4px',
                          background: 'var(--color-surface, #fff)',
                          fontSize: '12px',
                          cursor: 'pointer',
                          color: '#15803d',
                          marginTop: '3px',
                        }}
                      >
                        {t('users.actionEnable')}
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={() =>
                          u.role === 'admin' ? onDemote(u.user_id) : onPromote(u.user_id)
                        }
                        disabled={loading}
                        style={{
                          padding: '4px 10px',
                          border: '1px solid var(--color-border, #e2e8f0)',
                          borderRadius: '4px',
                          background: 'var(--color-surface, #fff)',
                          fontSize: '12px',
                          cursor: 'pointer',
                          color: '#3b82f6',
                          marginRight: '4px',
                        }}
                      >
                        {u.role === 'admin' ? t('users.actionDemote') : t('users.actionPromote')}
                      </button>
                      <button
                        onClick={() => onDisable(u.user_id)}
                        disabled={loading}
                        style={{
                          padding: '4px 10px',
                          border: '1px solid var(--color-border, #e2e8f0)',
                          borderRadius: '4px',
                          background: 'var(--color-surface, #fff)',
                          fontSize: '12px',
                          cursor: 'pointer',
                          color: '#b45309',
                        }}
                      >
                        {t('users.actionDisable')}
                      </button>
                    </>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {!loading && items.length === 0 && (
        <div style={{ padding: '20px', textAlign: 'center', color: 'var(--color-text-muted, #718096)' }}>
          {t('users.empty')}
        </div>
      )}
    </div>
  )
}

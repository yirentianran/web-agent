import { useTranslation } from 'react-i18next'

interface UsersFilterProps {
  q: string
  role: string
  status: string
  totalCount: number
  onQChange: (q: string) => void
  onRoleChange: (role: string) => void
  onStatusChange: (status: string) => void
}

export default function UsersFilter({
  q,
  role,
  status,
  totalCount,
  onQChange,
  onRoleChange,
  onStatusChange,
}: UsersFilterProps) {
  const { t } = useTranslation()

  return (
    <div className="ranking-panel">
      <div
        style={{
          display: 'flex',
          gap: '10px',
          alignItems: 'center',
          flexWrap: 'wrap',
        }}
      >
        <input
          type="text"
          placeholder={t('users.searchPlaceholder')}
          value={q}
          onChange={(e) => onQChange(e.target.value)}
          style={{
            padding: '7px 12px',
            border: '1px solid var(--color-border, #e2e8f0)',
            borderRadius: '6px',
            fontSize: '13px',
            width: '200px',
            background: 'var(--color-surface, #fff)',
            color: 'var(--color-text, #1a202c)',
          }}
        />
        <select
          value={role}
          onChange={(e) => onRoleChange(e.target.value)}
          style={{
            padding: '7px 12px',
            border: '1px solid var(--color-border, #e2e8f0)',
            borderRadius: '6px',
            fontSize: '13px',
            background: 'var(--color-surface, #fff)',
            color: 'var(--color-text, #1a202c)',
          }}
        >
          <option value="">{t('users.allRoles')}</option>
          <option value="admin">{t('users.roleAdmin')}</option>
          <option value="user">{t('users.roleUser')}</option>
        </select>
        <select
          value={status}
          onChange={(e) => onStatusChange(e.target.value)}
          style={{
            padding: '7px 12px',
            border: '1px solid var(--color-border, #e2e8f0)',
            borderRadius: '6px',
            fontSize: '13px',
            background: 'var(--color-surface, #fff)',
            color: 'var(--color-text, #1a202c)',
          }}
        >
          <option value="">{t('users.allStatuses')}</option>
          <option value="active">{t('users.statusActive')}</option>
          <option value="disabled">{t('users.statusDisabled')}</option>
        </select>
        <span
          style={{
            marginLeft: 'auto',
            fontSize: '12px',
            color: 'var(--color-text-muted, #718096)',
          }}
        >
          {t('users.totalCount', { count: totalCount })}
        </span>
      </div>
    </div>
  )
}

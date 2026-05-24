import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useUsersApi, type UsersFilters } from '../hooks/useUsersApi'
import UsersFilter from './users/UsersFilter'
import UsersTable from './users/UsersTable'
import ConfirmDialog from './users/ConfirmDialog'

type DialogType = 'disable' | 'enable' | 'promote' | 'demote' | null

interface PendingAction {
  type: DialogType
  userId: string
}

export default function UsersPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  const [filters, setFilters] = useState<UsersFilters>({
    q: '',
    role: '',
    status: '',
    sort: 'created_at',
    order: 'desc',
  })
  const [page, setPage] = useState(1)
  const [pending, setPending] = useState<PendingAction | null>(null)
  const [actionLoading, setActionLoading] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const api = useUsersApi(filters, page)
  const currentUserId = localStorage.getItem('userId') || ''

  const totalPages = api.list.data
    ? Math.ceil(api.list.data.total / api.list.data.page_size)
    : 0

  const confirmAction = useCallback(async () => {
    if (!pending) return
    setActionLoading(true)
    setActionError(null)
    try {
      const { type, userId } = pending
      if (type === 'disable') await api.disableUser(userId)
      else if (type === 'enable') await api.enableUser(userId)
      else if (type === 'promote') await api.promoteUser(userId)
      else if (type === 'demote') await api.demoteUser(userId)
      api.refetch()
      setPending(null)
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : t('users.actionError'))
    } finally {
      setActionLoading(false)
    }
  }, [pending, api, t])

  const cancelAction = useCallback(() => {
    setPending(null)
    setActionError(null)
  }, [])

  const triggerAction = useCallback((type: DialogType, userId: string) => {
    setPending({ type, userId })
    setActionError(null)
  }, [])

  const dialogTitle =
    pending?.type === 'disable'
      ? t('users.confirmDisableTitle')
      : pending?.type === 'enable'
        ? t('users.confirmEnableTitle')
        : pending?.type === 'promote'
          ? t('users.confirmPromoteTitle')
          : pending?.type === 'demote'
            ? t('users.confirmDemoteTitle')
            : ''

  const dialogMessageKey =
    pending?.type === 'disable'
      ? 'users.confirmDisableBody' as const
      : pending?.type === 'enable'
        ? 'users.confirmEnableBody' as const
        : pending?.type === 'promote'
          ? 'users.confirmPromoteBody' as const
          : 'users.confirmDemoteBody' as const

  const dialogConfirmLabel =
    pending?.type === 'disable'
      ? t('users.confirmDisableButton')
      : pending?.type === 'enable'
        ? t('users.confirmEnableButton')
        : pending?.type === 'promote'
          ? t('users.confirmPromoteButton')
          : t('users.confirmDemoteButton')

  const dialogConfirmClass =
    pending?.type === 'disable' || pending?.type === 'demote' ? 'danger' : 'primary'

  return (
    <div className="detail-page">
      <div className="detail-header">
        <button className="detail-back-btn" onClick={() => navigate('/')}>
          {t('users.back')}
        </button>
        <h2
          style={{
            fontSize: '1.25rem',
            fontWeight: 600,
            color: 'var(--color-text, #1a202c)',
            margin: 0,
          }}
        >
          {t('users.title')}
        </h2>
      </div>

      {api.list.error && (
        <div
          style={{
            padding: '12px 16px',
            background: '#fef2f2',
            border: '1px solid #fee2e2',
            borderRadius: '6px',
            color: '#dc2626',
            fontSize: '13px',
          }}
        >
          {api.list.error}
          <button
            onClick={api.refetch}
            style={{
              marginLeft: '12px',
              padding: '2px 10px',
              border: '1px solid #dc2626',
              borderRadius: '4px',
              background: '#fff',
              color: '#dc2626',
              cursor: 'pointer',
              fontSize: '12px',
            }}
          >
            {t('common.retry')}
          </button>
        </div>
      )}

      <UsersFilter
        q={filters.q}
        role={filters.role}
        status={filters.status}
        totalCount={api.list.data?.total ?? 0}
        onQChange={(q) => setFilters((f) => ({ ...f, q }))}
        onRoleChange={(role) => {
          setFilters((f) => ({ ...f, role }))
          setPage(1)
        }}
        onStatusChange={(status) => {
          setFilters((f) => ({ ...f, status }))
          setPage(1)
        }}
        onSearch={() => {
          setPage(1)
          api.refetch()
        }}
      />

      {api.list.loading && (
        <div
          style={{
            padding: '40px',
            textAlign: 'center',
            color: 'var(--color-text-muted, #718096)',
          }}
        >
          {t('common.loading')}
        </div>
      )}

      {!api.list.loading && (
        <UsersTable
          items={api.list.data?.items ?? []}
          currentUserId={currentUserId}
          loading={actionLoading}
          onDisable={(uid) => triggerAction('disable', uid)}
          onEnable={(uid) => triggerAction('enable', uid)}
          onPromote={(uid) => triggerAction('promote', uid)}
          onDemote={(uid) => triggerAction('demote', uid)}
        />
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            gap: '4px',
            marginTop: '16px',
          }}
        >
          <button
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            style={{
              padding: '5px 10px',
              border: '1px solid var(--color-border, #e2e8f0)',
              borderRadius: '6px',
              background: 'var(--color-surface, #fff)',
              fontSize: '12px',
              color: 'var(--color-text-muted, #64748b)',
              cursor: page <= 1 ? 'default' : 'pointer',
              opacity: page <= 1 ? 0.5 : 1,
            }}
          >
            ‹
          </button>
          {(() => {
            const maxVisible = Math.min(totalPages, 5)
            const startPage = Math.max(1, Math.min(page - 2, totalPages - maxVisible + 1))
            return Array.from({ length: maxVisible }, (_, i) => startPage + i).map((pageNum) => (
              <button
                key={pageNum}
                onClick={() => setPage(pageNum)}
                style={{
                  padding: '5px 10px',
                  background: pageNum === page ? '#eff6ff' : 'transparent',
                  color: pageNum === page ? '#3b82f6' : 'var(--color-text-muted, #64748b)',
                  borderRadius: '6px',
                  fontSize: '12px',
                  fontWeight: pageNum === page ? 600 : 400,
                  border: 'none',
                  cursor: 'pointer',
                }}
              >
                {pageNum}
              </button>
            ))
          })()}
          <button
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            style={{
              padding: '5px 10px',
              border: '1px solid var(--color-border, #e2e8f0)',
              borderRadius: '6px',
              background: 'var(--color-surface, #fff)',
              fontSize: '12px',
              color: 'var(--color-text-muted, #64748b)',
              cursor: page >= totalPages ? 'default' : 'pointer',
              opacity: page >= totalPages ? 0.5 : 1,
            }}
          >
            ›
          </button>
        </div>
      )}

      {pending && (
        <ConfirmDialog
          title={dialogTitle}
          userName={pending.userId}
          messageKey={dialogMessageKey}
          confirmLabel={dialogConfirmLabel}
          confirmClass={dialogConfirmClass}
          loading={actionLoading}
          onConfirm={confirmAction}
          onCancel={cancelAction}
        />
      )}

      {actionError && (
        <div
          style={{
            padding: '10px 16px',
            background: '#fef2f2',
            border: '1px solid #fee2e2',
            borderRadius: '6px',
            color: '#dc2626',
            fontSize: '13px',
            marginTop: '12px',
          }}
        >
          {actionError}
        </div>
      )}
    </div>
  )
}

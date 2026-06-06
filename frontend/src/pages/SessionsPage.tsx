import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useSessionsApi, PAGE_SIZE, type SessionsFilters, type SessionItem } from '../hooks/useSessionsApi'
import { fetchJson } from '../lib/api'
import { formatTokens } from '../lib/format'
import ChatArea from '../components/ChatArea'
import type { Message, SessionStatus } from '../lib/types'

function formatDate(ts: number): string {
  const d = new Date(ts * 1000)
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

const STATUS_OPTIONS = ['', 'running', 'completed', 'cancelled', 'error', 'idle', 'waiting_user']

export default function SessionsPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  const [filters, setFilters] = useState<SessionsFilters>({
    user_id: '', status: '', q: '', from_date: '', to_date: '', sort: 'created_at', order: 'desc',
  })
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [chatSid, setChatSid] = useState<string | null>(null)
  const [chatMsgs, setChatMsgs] = useState<Message[] | null>(null)
  const [chatMeta, setChatMeta] = useState<SessionItem | null>(null)

  const api = useSessionsApi(filters, page)

  const agg = api.aggregate.data

  const toggleSelect = (sid: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(sid) ? next.delete(sid) : next.add(sid)
      return next
    })
  }

  const toggleAll = () => {
    if (!api.list.data) return
    const all = api.list.data.items.map((s) => s.session_id)
    setSelected(selected.size === all.length ? new Set() : new Set(all))
  }

  const openChat = async (item: SessionItem) => {
    setChatSid(item.session_id)
    setChatMeta(item)
    try {
      const resp = await fetchJson<{ items: Array<Record<string, unknown>> }>(
        `/api/admin/sessions/${item.session_id}/messages?page_size=200`,
      )
      setChatMsgs((resp.items || []).map((raw) => ({
        type: raw.type as Message['type'],
        content: raw.content as string || '',
        index: raw.seq as number ?? 0,
        subtype: raw.subtype as string | undefined,
        name: raw.name as string | undefined,
        input: raw.input as unknown,
        data: raw.data as unknown,
        id: raw.id as string | undefined,
        clientMsgId: raw.client_msg_id as string || `msg-${raw.seq}`,
        is_error: raw.is_error as boolean | undefined,
        usage: raw.usage as Record<string, number> | undefined,
        session_id: item.session_id,
      })))
    } catch { setChatMsgs(null) }
  }

  const handleBatchCancel = async () => {
    if (!confirm(t('sessions.cancelSelected', { count: selected.size }))) return
    for (const sid of selected) {
      const item = api.list.data?.items.find((s) => s.session_id === sid)
      if (item?.status === 'running') {
        try { await api.cancelSession(sid, item.user_id) } catch { /* continue */ }
      }
    }
    setSelected(new Set())
    api.refetch()
  }

  const handleBatchDelete = async () => {
    if (!confirm(t('sessions.deleteSelected', { count: selected.size }))) return
    for (const sid of selected) {
      const item = api.list.data?.items.find((s) => s.session_id === sid)
      if (item) {
        try { await api.deleteSession(sid, item.user_id) } catch { /* continue */ }
      }
    }
    setSelected(new Set())
    api.refetch()
  }

  const statusLabel = (s: string) => {
    if (!s) return t('sessions.statusAll')
    const pascal = s.replace(/(^|_)([a-z])/g, (_, __, c) => c.toUpperCase())
    return t(`sessions.status${pascal}`)
  }

  const totalPages = Math.max(1, Math.ceil((api.list.data?.total || 0) / PAGE_SIZE))

  return (
    <div className="sessions-page detail-page">
      <div className="evolution-header skills-header detail-header">
        <button className="evolution-back-btn skills-back-btn detail-back-btn" onClick={() => navigate('/')} type="button">
          {t('common.back')}
        </button>
        <div className="evolution-header-title-group skills-header-title-group">
          <h2>{t('sessions.title')}</h2>
          <input type="text" placeholder={t('sessions.searchPlaceholder')} value={filters.q}
            onChange={(e) => { setFilters((f) => ({ ...f, q: e.target.value })); setPage(1) }}
            style={{ padding: '6px 12px', border: '1px solid var(--color-border)', borderRadius: 6, fontSize: '0.875rem', width: 240 }} />
        </div>
      </div>

      <div className="stats-cards" style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 16, marginBottom: 16 }}>
        <div className="stats-card" style={cardStyle}><span style={{ fontSize: '1.5rem' }}>📊</span><div><span style={{ fontSize: '1.5rem', fontWeight: 700, display: 'block' }}>{agg?.overview.total_sessions ?? '-'}</span><span style={{ fontSize: '0.8rem', color: 'var(--color-muted)' }}>{t('sessions.totalSessions')}</span></div></div>
        <div className="stats-card" style={cardStyle}><span style={{ fontSize: '1.5rem' }}>🟢</span><div><span style={{ fontSize: '1.5rem', fontWeight: 700, display: 'block' }}>{agg?.overview.active_sessions ?? '-'}</span><span style={{ fontSize: '0.8rem', color: 'var(--color-muted)' }}>{t('sessions.active')}</span></div></div>
        <div className="stats-card" style={cardStyle}><span style={{ fontSize: '1.5rem' }}>👥</span><div><span style={{ fontSize: '1.5rem', fontWeight: 700, display: 'block' }}>{agg?.overview.total_users ?? '-'}</span><span style={{ fontSize: '0.8rem', color: 'var(--color-muted)' }}>{t('sessions.users')}</span></div></div>
        <div className="stats-card" style={cardStyle}><span style={{ fontSize: '1.5rem' }}>🪙</span><div><span style={{ fontSize: '1.5rem', fontWeight: 700, display: 'block' }}>{agg ? formatTokens(agg.overview.total_tokens) : '-'}</span><span style={{ fontSize: '0.8rem', color: 'var(--color-muted)' }}>{t('sessions.tokens')}</span></div></div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
        <div style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 8, overflow: 'hidden' }}>
          <h4 style={{ padding: '10px 14px', fontSize: '0.85rem', fontWeight: 600, borderBottom: '1px solid var(--color-border)', margin: 0 }}>{t('sessions.byUser')}</h4>
          <table style={{ width: '100%', fontSize: '0.8rem', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: '#fafbfc' }}><th style={thStyle}>{t('sessions.colUser')}</th><th style={{ ...thStyle, textAlign: 'right' }}>{t('sessions.title')}</th><th style={{ ...thStyle, textAlign: 'right' }}>{t('sessions.tokens')}</th></tr></thead>
            <tbody>
              {(agg?.by_user || []).slice(0, 6).map((u) => (
                <tr key={u.user_id}><td style={tdStyle}>{u.user_id}</td><td style={{ ...tdStyle, textAlign: 'right' }}>{u.session_count}</td><td style={{ ...tdStyle, textAlign: 'right' }}>{formatTokens(u.total_tokens)}</td></tr>
              ))}
              {(!agg?.by_user || agg.by_user.length === 0) && <tr><td colSpan={3} style={{ ...tdStyle, textAlign: 'center', color: 'var(--color-muted)' }}>{t('sessions.noData')}</td></tr>}
            </tbody>
          </table>
        </div>
        <div style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 8, overflow: 'hidden' }}>
          <h4 style={{ padding: '10px 14px', fontSize: '0.85rem', fontWeight: 600, borderBottom: '1px solid var(--color-border)', margin: 0 }}>{t('sessions.byDate')}</h4>
          <table style={{ width: '100%', fontSize: '0.8rem', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: '#fafbfc' }}><th style={thStyle}>Date</th><th style={{ ...thStyle, textAlign: 'right' }}>{t('sessions.title')}</th><th style={{ ...thStyle, textAlign: 'right' }}>{t('sessions.tokens')}</th></tr></thead>
            <tbody>
              {(agg?.by_date || []).slice(0, 7).map((d) => (
                <tr key={d.date}><td style={tdStyle}>{d.date}</td><td style={{ ...tdStyle, textAlign: 'right' }}>{d.session_count}</td><td style={{ ...tdStyle, textAlign: 'right' }}>{formatTokens(d.total_tokens)}</td></tr>
              ))}
              {(!agg?.by_date || agg.by_date.length === 0) && <tr><td colSpan={3} style={{ ...tdStyle, textAlign: 'center', color: 'var(--color-muted)' }}>{t('sessions.noData')}</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        <select value={filters.user_id} onChange={(e) => { setFilters((f) => ({ ...f, user_id: e.target.value })); setPage(1) }}
          style={{ padding: '6px 10px', border: '1px solid var(--color-border)', borderRadius: 6, background: 'var(--color-surface)', fontSize: '0.85rem' }}>
          <option value="">{t('sessions.allUsers')}</option>
          {(agg?.by_user || []).map((u) => <option key={u.user_id} value={u.user_id}>{u.user_id}</option>)}
        </select>
        <select value={filters.status} onChange={(e) => { setFilters((f) => ({ ...f, status: e.target.value })); setPage(1) }}
          style={{ padding: '6px 10px', border: '1px solid var(--color-border)', borderRadius: 6, background: 'var(--color-surface)', fontSize: '0.85rem' }}>
          {STATUS_OPTIONS.map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}
        </select>
        <input type="date" value={filters.from_date} onChange={(e) => { setFilters((f) => ({ ...f, from_date: e.target.value })); setPage(1) }}
          style={{ padding: '6px 10px', border: '1px solid var(--color-border)', borderRadius: 6, background: 'var(--color-surface)', fontSize: '0.85rem' }} />
        <span style={{ color: 'var(--color-muted)' }}>{t('sessions.to')}</span>
        <input type="date" value={filters.to_date} onChange={(e) => { setFilters((f) => ({ ...f, to_date: e.target.value })); setPage(1) }}
          style={{ padding: '6px 10px', border: '1px solid var(--color-border)', borderRadius: 6, background: 'var(--color-surface)', fontSize: '0.85rem' }} />
        <select value={`${filters.sort}:${filters.order}`} onChange={(e) => { const [s, o] = e.target.value.split(':'); setFilters((f) => ({ ...f, sort: s, order: o })); setPage(1) }}
          style={{ padding: '6px 10px', border: '1px solid var(--color-border)', borderRadius: 6, background: 'var(--color-surface)', fontSize: '0.85rem' }}>
          <option value="created_at:desc">{t('sessions.newest')}</option><option value="created_at:asc">{t('sessions.oldest')}</option>
          <option value="message_count:desc">{t('sessions.mostMsgs')}</option><option value="total_tokens:desc">{t('sessions.mostTokens')}</option>
        </select>
        <span style={{ marginLeft: 'auto', fontSize: '0.85rem', color: 'var(--color-muted)' }}>
          {api.list.data ? t('sessions.total', { count: api.list.data.total }) : ''}{selected.size > 0 ? ` · ${t('sessions.selected', { count: selected.size })}` : ''}
        </span>
      </div>

      {selected.size >= 2 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px', background: 'var(--color-primary-light)', border: '1px solid #c7d2fe', borderRadius: 8, marginBottom: 12, fontSize: '0.85rem' }}>
          <span>{t('sessions.selected', { count: selected.size })}</span>
          <button onClick={handleBatchCancel} style={batchBtnStyle}>{t('common.cancel')}</button>
          <button onClick={handleBatchDelete} style={{ ...batchBtnStyle, borderColor: 'var(--color-danger)', color: 'var(--color-danger)' }}>{t('common.delete')}</button>
        </div>
      )}

      <div style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 8, overflow: 'hidden', marginBottom: 16 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem', tableLayout: 'fixed' }}>
          <colgroup>
            <col style={{ width: 32 }} /><col style={{ width: 170 }} /><col style={{ width: 80 }} />
            <col /><col style={{ width: 100 }} /><col style={{ width: 50 }} />
            <col style={{ width: 70 }} /><col style={{ width: 90 }} /><col style={{ width: 90 }} />
          </colgroup>
          <thead>
            <tr><th style={thStyle}><input type="checkbox" onChange={toggleAll} checked={api.list.data ? selected.size === api.list.data.items.length && api.list.data.items.length > 0 : false} /></th><th style={thStyle}>{t('sessions.colSessionId')}</th><th style={thStyle}>{t('sessions.colUser')}</th><th style={thStyle}>{t('sessions.colTitle')}</th><th style={thStyle}>{t('sessions.colStatus')}</th><th style={{ ...thStyle, textAlign: 'right' }}>{t('sessions.colMsgs')}</th><th style={{ ...thStyle, textAlign: 'right' }}>{t('sessions.colTokens')}</th><th style={thStyle}>{t('sessions.colCreated')}</th><th style={thStyle}>{t('sessions.colLastActive')}</th></tr>
          </thead>
          <tbody>
            {api.list.loading && <tr><td colSpan={9} style={{ ...tdStyle, textAlign: 'center' }}>{t('sessions.loading')}</td></tr>}
            {api.list.error && <tr><td colSpan={9} style={{ ...tdStyle, textAlign: 'center', color: 'var(--color-danger)' }}>{api.list.error}</td></tr>}
            {(api.list.data?.items || []).map((item) => (
              <tr key={item.session_id} onClick={() => openChat(item)} style={{ cursor: 'pointer' }}>
                <td style={tdStyle} onClick={(e) => e.stopPropagation()}><input type="checkbox" checked={selected.has(item.session_id)} onChange={() => toggleSelect(item.session_id)} /></td>
                <td style={{ ...tdStyle, fontFamily: 'monospace', fontSize: '0.8rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.session_id}</td>
                <td style={tdStyle}>{item.user_id}</td>
                <td style={{ ...tdStyle, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.title || '—'}</td>
                <td style={tdStyle}><span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 999, fontSize: '0.75rem', fontWeight: 500, ...statusStyle(item.status) }}>{statusLabel(item.status)}</span></td>
                <td style={{ ...tdStyle, textAlign: 'right' }}>{item.message_count}</td>
                <td style={{ ...tdStyle, textAlign: 'right' }}>{formatTokens(item.total_tokens)}</td>
                <td style={{ ...tdStyle, fontSize: '0.8rem' }}>{formatDate(item.created_at)}</td>
                <td style={{ ...tdStyle, fontSize: '0.8rem' }}>{formatDate(item.last_active_at)}</td>
              </tr>
            ))}
            {!api.list.loading && !api.list.error && (api.list.data?.items.length === 0) && (
              <tr><td colSpan={9} style={{ ...tdStyle, textAlign: 'center', color: 'var(--color-muted)' }}>{t('sessions.noSessionsFound')}</td></tr>
            )}
          </tbody>
        </table>

        <div style={{ display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'center', padding: 16, fontSize: '0.85rem' }}>
          <button disabled={page <= 1} onClick={() => setPage(page - 1)} style={pageBtnStyle}>←</button>
          {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
            const start = Math.max(1, Math.min(page - 2, totalPages - 4))
            const p = start + i
            if (p > totalPages) return null
            return <button key={p} onClick={() => setPage(p)} style={{ ...pageBtnStyle, ...(p === page ? { background: 'var(--color-primary)', color: '#fff', borderColor: 'var(--color-primary)' } : {}) }}>{p}</button>
          })}
          <button disabled={page >= totalPages} onClick={() => setPage(page + 1)} style={pageBtnStyle}>→</button>
          <span style={{ color: 'var(--color-muted)', marginLeft: 12 }}>{t('sessions.total', { count: api.list.data?.total || 0 })}</span>
        </div>
      </div>

      {chatSid && chatMeta && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 100, display: 'flex', flexDirection: 'column', background: 'var(--color-bg)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 20px', borderBottom: '1px solid var(--color-border)', flexShrink: 0, background: 'var(--color-surface)' }}>
            <div><h3 style={{ fontSize: '1rem', margin: 0 }}>{chatMeta.title || chatMeta.session_id}</h3><span style={{ fontSize: '0.75rem', color: 'var(--color-muted)' }}>{t('sessions.chatMetaInfo', { sessionId: chatMeta.session_id, userId: chatMeta.user_id, msgCount: chatMeta.message_count, tokens: formatTokens(chatMeta.total_tokens) })}</span></div>
            <button onClick={() => { setChatSid(null); setChatMsgs(null); }} style={{ background: 'none', border: 'none', fontSize: '1.2rem', cursor: 'pointer', color: 'var(--color-muted)' }}>✕</button>
          </div>
          <div style={{ flex: 1, minHeight: 0 }}>
            {chatMsgs === null ? (
              <div style={{ padding: 40, textAlign: 'center', color: 'var(--color-muted)' }}>{t('sessions.loadingMessages')}</div>
            ) : chatMsgs.length === 0 ? (
              <div style={{ padding: 40, textAlign: 'center', color: 'var(--color-muted)' }}>{t('sessions.noMessagesFound')}</div>
            ) : (
              <ChatArea
                key={chatSid}
                messages={chatMsgs}
                sessionId={chatSid}
                sessionState={(chatMeta.status as SessionStatus) || 'completed'}
                onAnswer={() => {}}
                scrollPositions={new Map()}
              />
            )}
          </div>
        </div>
      )}
    </div>
  )
}

const cardStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 12, padding: '16px 20px', background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 8 }

const thStyle: React.CSSProperties = { textAlign: 'left', padding: '10px 12px', borderBottom: '2px solid var(--color-border)', fontWeight: 600, color: 'var(--color-muted)', background: '#fafbfc', whiteSpace: 'nowrap' }
const tdStyle: React.CSSProperties = { padding: '10px 12px', borderBottom: '1px solid var(--color-border)' }

const batchBtnStyle: React.CSSProperties = { padding: '6px 14px', borderRadius: 6, border: '1px solid var(--color-primary)', color: 'var(--color-primary)', background: '#fff', cursor: 'pointer', fontSize: '0.8rem' }
const pageBtnStyle: React.CSSProperties = { padding: '4px 10px', border: '1px solid var(--color-border)', borderRadius: 4, background: 'var(--color-surface)', cursor: 'pointer' }

function statusStyle(status: string): React.CSSProperties {
  switch (status) {
    case 'running': return { background: '#dcfce7', color: '#166534' }
    case 'completed': return { background: '#dbeafe', color: '#1e40af' }
    case 'cancelled': return { background: '#fef3c7', color: '#92400e' }
    case 'error': return { background: '#fee2e2', color: '#991b1b' }
    default: return { background: '#f1f5f9', color: '#475569' }
  }
}
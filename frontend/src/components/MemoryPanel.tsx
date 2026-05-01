import { useState, useEffect, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

interface MemoryPanelProps {
  userId: string
  authToken?: string | null
  onBack: () => void
}

type StatusMsg = { text: string; ok: boolean } | null

export default function MemoryPanel({ userId, authToken, onBack }: MemoryPanelProps) {
  const { t } = useTranslation()
  const [notes, setNotes] = useState<Array<{ filename: string; size_bytes: number; modified_at: number }>>([])
  const [selectedNote, setSelectedNote] = useState<string | null>(null)
  const [noteContent, setNoteContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState<StatusMsg>(null)
  const [activeTab, setActiveTab] = useState<'platform' | 'agent'>('platform')

  const [prefKey, setPrefKey] = useState('')
  const [prefVal, setPrefVal] = useState('')
  const [entityKey, setEntityKey] = useState('')
  const [entityVal, setEntityVal] = useState('')
  const [platformMemory, setPlatformMemory] = useState<Record<string, unknown>>({})
  const [editingPref, setEditingPref] = useState<string | null>(null)
  const [editingEntity, setEditingEntity] = useState<string | null>(null)

  const headers: Record<string, string> = {}
  if (authToken) headers['Authorization'] = `Bearer ${authToken}`

  useEffect(() => { loadPlatformMemory(); loadNotes() }, [userId])

  const showStatus = (text: string, ok: boolean) => {
    setStatus({ text, ok })
    setTimeout(() => setStatus(null), 2500)
  }

  const loadPlatformMemory = async () => {
    try {
      const resp = await fetch(`/api/users/${userId}/memory`, { headers })
      if (resp.ok) setPlatformMemory(await resp.json())
    } catch { /* ignore */ }
  }

  const loadNotes = async () => {
    try {
      const resp = await fetch(`/api/users/${userId}/memory/agent-notes`, { headers })
      if (resp.ok) setNotes(await resp.json())
    } catch { /* ignore */ }
  }

  const handleNoteSelect = async (filename: string) => {
    setSelectedNote(filename)
    try {
      const resp = await fetch(`/api/users/${userId}/memory/agent-notes/${filename}`, { headers })
      if (resp.ok) {
        const data = await resp.json()
        setNoteContent(data.content || '')
      }
    } catch { /* ignore */ }
  }

  const handleSaveNote = async (e: FormEvent) => {
    e.preventDefault()
    if (!selectedNote) return
    setLoading(true)
    try {
      const resp = await fetch(`/api/users/${userId}/memory/agent-notes/${selectedNote}`, {
        method: 'PUT',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: noteContent }),
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      await loadNotes()
      showStatus(t('memory.saved'), true)
    } catch (err) {
      showStatus(err instanceof Error ? err.message : t('memory.saveFailed'), false)
    }
    setLoading(false)
  }

  const handleNewNote = () => {
    const name = prompt(t('memory.noteNamePrompt'))
    if (!name) return
    const filename = name.endsWith('.md') ? name : `${name}.md`
    setSelectedNote(filename)
    setNoteContent('')
  }

  const handleDeleteNote = async (filename: string) => {
    if (!confirm(t('memory.confirmDeleteNote', { filename }))) return
    try {
      const resp = await fetch(`/api/users/${userId}/memory/agent-notes/${filename}`, {
        method: 'DELETE',
        headers,
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      if (selectedNote === filename) {
        setSelectedNote(null)
        setNoteContent('')
      }
      await loadNotes()
      showStatus(t('memory.deleted'), true)
    } catch (err) {
      showStatus(err instanceof Error ? err.message : t('memory.deleteFailed'), false)
    }
  }

  const handleSavePlatformMemory = async () => {
    setLoading(true)
    const patch: Record<string, unknown> = {}
    if (prefKey.trim()) patch['preferences'] = { [prefKey.trim()]: prefVal }
    if (entityKey.trim()) patch['entity_memory'] = { [entityKey.trim()]: entityVal }
    if (Object.keys(patch).length === 0) {
      showStatus(t('memory.nothingToSave'), false)
      setLoading(false)
      return
    }
    try {
      const resp = await fetch(`/api/users/${userId}/memory`, {
        method: 'PUT',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      await loadPlatformMemory()
      setPrefKey('')
      setPrefVal('')
      setEntityKey('')
      setEntityVal('')
      showStatus(t('memory.platformUpdated'), true)
    } catch (err) {
      showStatus(err instanceof Error ? err.message : t('memory.saveFailed'), false)
    }
    setLoading(false)
  }

  const handleDeletePlatformKey = async (category: 'preferences' | 'entity_memory', key: string) => {
    if (!confirm(t('memory.confirmDeleteKey', { key }))) return
    setLoading(true)
    try {
      const resp = await fetch(`/api/users/${userId}/memory`, {
        method: 'PUT',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ [category]: { [key]: null } }),
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      await loadPlatformMemory()
      showStatus(t('memory.deleted'), true)
    } catch (err) {
      showStatus(err instanceof Error ? err.message : t('memory.deleteFailed'), false)
    }
    setLoading(false)
  }

  const startEditPref = (key: string, value: unknown) => {
    setPrefKey(key)
    setPrefVal(String(value))
    setEditingPref(key)
  }

  const startEditEntity = (key: string, value: unknown) => {
    setEntityKey(key)
    setEntityVal(String(value))
    setEditingEntity(key)
  }

  const cancelEditPref = () => {
    setPrefKey('')
    setPrefVal('')
    setEditingPref(null)
  }

  const cancelEditEntity = () => {
    setEntityKey('')
    setEntityVal('')
    setEditingEntity(null)
  }

  const preferences = (platformMemory.preferences as Record<string, unknown>) || {}
  const entityMemory = (platformMemory.entity_memory as Record<string, unknown>) || {}

  return (
    <div className="memory-page feedback-page">
      <div className="memory-header feedback-header">
        <button className="memory-back-btn feedback-back-btn" onClick={onBack}>&larr; {t('common.back')}</button>
        <h2>{t('memory.title')}</h2>
      </div>

      {status && (
        <div className={`memory-status ${status.ok ? 'memory-status-ok' : 'memory-status-err'}`}>
          {status.text}
        </div>
      )}

      <div className="memory-tabs">
        {(['platform', 'agent'] as const).map(tabVal => (
          <button
            key={tabVal}
            className={`memory-tab ${activeTab === tabVal ? 'active' : ''}`}
            onClick={() => setActiveTab(tabVal)}
          >
            {tabVal === 'platform' ? t('memory.platformTab') : t('memory.agentTab')}
          </button>
        ))}
      </div>

      {activeTab === 'platform' && (
        <div className="memory-view feedback-section">
          <p className="memory-hint">
            {t('memory.platformHint')}
          </p>

          <h3 className="feedback-section-title">{t('memory.preferencesTitle')}</h3>
          {Object.keys(preferences).length > 0 ? (
            <table className="memory-kv-table">
              <tbody>
                {Object.entries(preferences).map(([k, v]) => (
                  <tr key={k} className={`kv-row ${editingPref === k ? 'kv-row-editing' : ''}`} onClick={() => startEditPref(k, v)} title={t('memory.clickToEdit')}>
                    <td className="kv-key">{k}</td>
                    <td className="kv-val">{String(v)}</td>
                    <td className="kv-actions">
                      <button className="btn-kv-delete" onClick={(e) => { e.stopPropagation(); handleDeletePlatformKey('preferences', k) }} title={t('common.delete')}>✕</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="memory-empty-hint">{t('memory.noPreferences')}</p>
          )}
          <div className="memory-kv-input">
            <input placeholder={t('memory.keyPlaceholder')} value={prefKey} onChange={(e) => setPrefKey(e.target.value)} />
            <input placeholder={t('memory.valuePlaceholder')} value={prefVal} onChange={(e) => setPrefVal(e.target.value)} />
            {editingPref && (
              <button className="btn-kv-cancel" onClick={cancelEditPref} type="button">{t('common.cancel')}</button>
            )}
          </div>

          <h3 className="feedback-section-title" style={{ marginTop: 20 }}>{t('memory.entityTitle')}</h3>
          {Object.keys(entityMemory).length > 0 ? (
            <table className="memory-kv-table">
              <tbody>
                {Object.entries(entityMemory).map(([k, v]) => (
                  <tr key={k} className={`kv-row ${editingEntity === k ? 'kv-row-editing' : ''}`} onClick={() => startEditEntity(k, v)} title={t('memory.clickToEdit')}>
                    <td className="kv-key">{k}</td>
                    <td className="kv-val">{String(v)}</td>
                    <td className="kv-actions">
                      <button className="btn-kv-delete" onClick={(e) => { e.stopPropagation(); handleDeletePlatformKey('entity_memory', k) }} title={t('common.delete')}>✕</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="memory-empty-hint">{t('memory.noEntity')}</p>
          )}
          <div className="memory-kv-input">
            <input placeholder={t('memory.entityKeyPlaceholder')} value={entityKey} onChange={(e) => setEntityKey(e.target.value)} />
            <input placeholder={t('memory.entityValuePlaceholder')} value={entityVal} onChange={(e) => setEntityVal(e.target.value)} />
            {editingEntity && (
              <button className="btn-kv-cancel" onClick={cancelEditEntity} type="button">{t('common.cancel')}</button>
            )}
          </div>

          <button className="btn-save-memory" onClick={handleSavePlatformMemory} disabled={loading} style={{ marginTop: 16 }}>
            {loading ? t('common.saving') : editingPref || editingEntity ? t('memory.updateButton') : t('memory.addButton')}
          </button>
        </div>
      )}

      {activeTab === 'agent' && (
        <div className="agent-notes-view feedback-section">
          <p className="memory-hint">
            {t('memory.agentHint')}
          </p>
          <div className="notes-header">
            <button className="btn-new-note" onClick={handleNewNote}>{t('memory.newNote')}</button>
          </div>
          <div className="notes-layout">
            <ul className="notes-list">
              {notes.map(n => (
                <li
                  key={n.filename}
                  className={`note-item ${selectedNote === n.filename ? 'active' : ''}`}
                  onClick={() => handleNoteSelect(n.filename)}
                >
                  <span className="note-name">{n.filename}</span>
                  <span className="note-size">{(n.size_bytes / 1024).toFixed(1)} KB</span>
                  <button className="btn-delete-note" onClick={(e) => { e.stopPropagation(); handleDeleteNote(n.filename) }} title={t('common.delete')}>✕</button>
                </li>
              ))}
              {notes.length === 0 && <li className="note-empty">{t('memory.noNotes', { buttonText: t('memory.newNote') })}</li>}
            </ul>
            {selectedNote && (
              <form className="note-editor" onSubmit={handleSaveNote}>
                <div className="note-editor-header">
                  <span className="note-editor-filename">{selectedNote}</span>
                </div>
                <textarea
                  value={noteContent}
                  onChange={(e) => setNoteContent(e.target.value)}
                  placeholder={t('memory.writePlaceholder')}
                  className="note-textarea"
                />
                <button type="submit" className="btn-save-note" disabled={loading}>
                  {loading ? t('common.saving') : t('common.save')}
                </button>
              </form>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

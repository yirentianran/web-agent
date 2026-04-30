import { useState, useEffect, type FormEvent } from 'react'

interface MemoryPanelProps {
  userId: string
  authToken?: string | null
  onBack: () => void
}

type StatusMsg = { text: string; ok: boolean } | null

export default function MemoryPanel({ userId, authToken, onBack }: MemoryPanelProps) {
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
      showStatus('Saved', true)
    } catch (err) {
      showStatus(err instanceof Error ? err.message : 'Save failed', false)
    }
    setLoading(false)
  }

  const handleNewNote = () => {
    const name = prompt('Note name:')
    if (!name) return
    const filename = name.endsWith('.md') ? name : `${name}.md`
    setSelectedNote(filename)
    setNoteContent('')
  }

  const handleDeleteNote = async (filename: string) => {
    if (!confirm(`Delete "${filename}"?`)) return
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
      showStatus('Deleted', true)
    } catch (err) {
      showStatus(err instanceof Error ? err.message : 'Delete failed', false)
    }
  }

  const handleSavePlatformMemory = async () => {
    setLoading(true)
    const patch: Record<string, unknown> = {}
    if (prefKey.trim()) patch['preferences'] = { [prefKey.trim()]: prefVal }
    if (entityKey.trim()) patch['entity_memory'] = { [entityKey.trim()]: entityVal }
    if (Object.keys(patch).length === 0) {
      showStatus('Nothing to save', false)
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
      showStatus('Platform memory updated', true)
    } catch (err) {
      showStatus(err instanceof Error ? err.message : 'Save failed', false)
    }
    setLoading(false)
  }

  const handleDeletePlatformKey = async (category: 'preferences' | 'entity_memory', key: string) => {
    if (!confirm(`Delete "${key}"?`)) return
    setLoading(true)
    try {
      const resp = await fetch(`/api/users/${userId}/memory`, {
        method: 'PUT',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ [category]: { [key]: null } }),
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      await loadPlatformMemory()
      showStatus('Deleted', true)
    } catch (err) {
      showStatus(err instanceof Error ? err.message : 'Delete failed', false)
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
        <button className="memory-back-btn feedback-back-btn" onClick={onBack}>&larr; Back</button>
        <h2>Memory Management</h2>
      </div>

      {status && (
        <div className={`memory-status ${status.ok ? 'memory-status-ok' : 'memory-status-err'}`}>
          {status.text}
        </div>
      )}

      <div className="memory-tabs">
        {(['platform', 'agent'] as const).map(t => (
          <button
            key={t}
            className={`memory-tab ${activeTab === t ? 'active' : ''}`}
            onClick={() => setActiveTab(t)}
          >
            {t === 'platform' ? 'Platform Memory (L1)' : 'Agent Notes (L2)'}
          </button>
        ))}
      </div>

      {activeTab === 'platform' && (
        <div className="memory-view feedback-section">
          <p className="memory-hint">
            Platform memory is automatically injected into every agent conversation. Set preferences to customize agent behavior, and entity memory for domain knowledge.
          </p>

          <h3 className="feedback-section-title">Preferences</h3>
          {Object.keys(preferences).length > 0 ? (
            <table className="memory-kv-table">
              <tbody>
                {Object.entries(preferences).map(([k, v]) => (
                  <tr key={k} className={`kv-row ${editingPref === k ? 'kv-row-editing' : ''}`} onClick={() => startEditPref(k, v)} title="Click to edit">
                    <td className="kv-key">{k}</td>
                    <td className="kv-val">{String(v)}</td>
                    <td className="kv-actions">
                      <button className="btn-kv-delete" onClick={(e) => { e.stopPropagation(); handleDeletePlatformKey('preferences', k) }} title="Delete">✕</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="memory-empty-hint">No preferences set.</p>
          )}
          <div className="memory-kv-input">
            <input placeholder="Key (e.g. language)" value={prefKey} onChange={(e) => setPrefKey(e.target.value)} />
            <input placeholder="Value (e.g. Chinese)" value={prefVal} onChange={(e) => setPrefVal(e.target.value)} />
            {editingPref && (
              <button className="btn-kv-cancel" onClick={cancelEditPref} type="button">Cancel</button>
            )}
          </div>

          <h3 className="feedback-section-title" style={{ marginTop: 20 }}>Entity Memory</h3>
          {Object.keys(entityMemory).length > 0 ? (
            <table className="memory-kv-table">
              <tbody>
                {Object.entries(entityMemory).map(([k, v]) => (
                  <tr key={k} className={`kv-row ${editingEntity === k ? 'kv-row-editing' : ''}`} onClick={() => startEditEntity(k, v)} title="Click to edit">
                    <td className="kv-key">{k}</td>
                    <td className="kv-val">{String(v)}</td>
                    <td className="kv-actions">
                      <button className="btn-kv-delete" onClick={(e) => { e.stopPropagation(); handleDeletePlatformKey('entity_memory', k) }} title="Delete">✕</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="memory-empty-hint">No entity memory set.</p>
          )}
          <div className="memory-kv-input">
            <input placeholder="Key (e.g. company_name)" value={entityKey} onChange={(e) => setEntityKey(e.target.value)} />
            <input placeholder="Value (e.g. Acme Corp)" value={entityVal} onChange={(e) => setEntityVal(e.target.value)} />
            {editingEntity && (
              <button className="btn-kv-cancel" onClick={cancelEditEntity} type="button">Cancel</button>
            )}
          </div>

          <button className="btn-save-memory" onClick={handleSavePlatformMemory} disabled={loading} style={{ marginTop: 16 }}>
            {loading ? 'Saving...' : editingPref || editingEntity ? 'Update' : 'Add'}
          </button>
        </div>
      )}

      {activeTab === 'agent' && (
        <div className="agent-notes-view feedback-section">
          <p className="memory-hint">
            Agent notes are Markdown files auto-loaded into the system prompt. Use them to give the agent persistent knowledge, instructions, or reference material.
          </p>
          <div className="notes-header">
            <button className="btn-new-note" onClick={handleNewNote}>+ New Note</button>
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
                  <button className="btn-delete-note" onClick={(e) => { e.stopPropagation(); handleDeleteNote(n.filename) }} title="Delete">✕</button>
                </li>
              ))}
              {notes.length === 0 && <li className="note-empty">No notes yet. Click "+ New Note" to create one.</li>}
            </ul>
            {selectedNote && (
              <form className="note-editor" onSubmit={handleSaveNote}>
                <div className="note-editor-header">
                  <span className="note-editor-filename">{selectedNote}</span>
                </div>
                <textarea
                  value={noteContent}
                  onChange={(e) => setNoteContent(e.target.value)}
                  placeholder="Write in Markdown..."
                  className="note-textarea"
                />
                <button type="submit" className="btn-save-note" disabled={loading}>
                  {loading ? 'Saving...' : 'Save'}
                </button>
              </form>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

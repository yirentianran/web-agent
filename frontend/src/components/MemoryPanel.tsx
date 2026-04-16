import { useState, useEffect, type FormEvent } from 'react'

interface MemoryPanelProps {
  userId: string
  authToken?: string | null
}

export default function MemoryPanel({ userId, authToken }: MemoryPanelProps) {
  const [memory, setMemory] = useState<Record<string, unknown>>({})
  const [notes, setNotes] = useState<Array<{ filename: string; size_bytes: number; modified_at: number }>>([])
  const [selectedNote, setSelectedNote] = useState<string | null>(null)
  const [noteContent, setNoteContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [activeTab, setActiveTab] = useState<'platform' | 'agent'>('platform')

  const headers: Record<string, string> = {}
  if (authToken) headers['Authorization'] = `Bearer ${authToken}`

  useEffect(() => { loadMemory() }, [userId])
  useEffect(() => { if (activeTab === 'agent') loadNotes() }, [userId, activeTab])

  const loadMemory = async () => {
    try {
      const resp = await fetch(`/api/users/${userId}/memory`, { headers })
      if (resp.ok) setMemory(await resp.json())
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
      await fetch(`/api/users/${userId}/memory/agent-notes/${selectedNote}`, {
        method: 'PUT',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: noteContent }),
      })
      await loadNotes()
    } catch { /* ignore */ }
    setLoading(false)
  }

  const handleNewNote = () => {
    const name = prompt('Note filename (e.g., findings.md):')
    if (!name) return
    setSelectedNote(name)
    setNoteContent('')
  }

  return (
    <div className="memory-panel">
      <div className="memory-tabs">
        <button
          className={`memory-tab ${activeTab === 'platform' ? 'active' : ''}`}
          onClick={() => setActiveTab('platform')}
        >
          Platform Memory
        </button>
        <button
          className={`memory-tab ${activeTab === 'agent' ? 'active' : ''}`}
          onClick={() => setActiveTab('agent')}
        >
          Agent Notes
        </button>
      </div>

      {activeTab === 'platform' && (
        <div className="memory-view">
          <pre className="memory-json">{JSON.stringify(memory, null, 2)}</pre>
        </div>
      )}

      {activeTab === 'agent' && (
        <div className="agent-notes-view">
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
                  {n.filename}
                  <span className="note-size">{(n.size_bytes / 1024).toFixed(1)} KB</span>
                </li>
              ))}
              {notes.length === 0 && <li className="note-empty">No notes yet</li>}
            </ul>
            {selectedNote && (
              <form className="note-editor" onSubmit={handleSaveNote}>
                <textarea
                  value={noteContent}
                  onChange={(e) => setNoteContent(e.target.value)}
                  placeholder="Write agent memory in Markdown..."
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

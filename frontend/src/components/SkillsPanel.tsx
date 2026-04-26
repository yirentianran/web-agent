import { useState, useCallback, useEffect, useRef } from 'react'
import type { Skill } from '../lib/types'
import { useSkillsApi } from '../hooks/useSkillsApi'

interface SkillsPanelProps {
  authToken: string | null
  userId: string
  onClose: () => void
  embedded?: boolean
}

type Tab = 'shared' | 'personal'

export default function SkillsPanel({ authToken, userId, onClose, embedded }: SkillsPanelProps) {
  const api = useSkillsApi(authToken, userId)
  const [tab, setTab] = useState<Tab>('personal')
  const [sharedSkills, setSharedSkills] = useState<Skill[]>([])
  const [personalSkills, setPersonalSkills] = useState<Skill[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [promoting, setPromoting] = useState<string | null>(null)
  const [viewingSkill, setViewingSkill] = useState<Skill | null>(null)
  const zipInputRef = useRef<HTMLInputElement>(null)

  const fetchSkills = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [shared, personal] = await Promise.all([api.listShared(), api.listPersonal()])
      setSharedSkills(shared)
      setPersonalSkills(personal)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load skills')
    } finally {
      setLoading(false)
    }
  }, [api])

  useEffect(() => {
    fetchSkills()
  }, [fetchSkills])

  const handleUpload = useCallback(async (file: File) => {
    setUploading(true)
    setError('')
    try {
      if (tab === 'shared') {
        await api.uploadShared(file)
      } else {
        await api.uploadPersonal(file)
      }
      await fetchSkills()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to upload skill')
    } finally {
      setUploading(false)
    }
  }, [api, fetchSkills, tab])

  const handleDelete = useCallback(async (name: string) => {
    if (!confirm(`Delete skill "${name}"? This cannot be undone.`)) return
    try {
      if (tab === 'shared') {
        await api.deleteShared(name)
      } else {
        await api.delete(name)
      }
      await fetchSkills()
      setViewingSkill(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete skill')
    }
  }, [api, fetchSkills, tab])

  const handlePromote = useCallback(async (name: string) => {
    if (!confirm(`Promote "${name}" to shared? This makes it available to all users.`)) return
    setPromoting(name)
    try {
      await api.promote(name)
      await fetchSkills()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to promote skill')
    } finally {
      setPromoting(null)
    }
  }, [api, fetchSkills])

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) handleUpload(f)
    e.target.value = ''
  }

  const skills = tab === 'shared' ? sharedSkills : personalSkills
  const isPersonal = tab === 'personal'

  // ── Detail View ────────────────────────────────────────────────

  if (viewingSkill) {
    return (
      <div className={`skills-panel ${embedded ? 'embedded' : ''}`}>
        <div className="skill-view">
          <div className="skill-view-header">
            <button className="btn-back" onClick={() => setViewingSkill(null)} type="button">
              ← Back
            </button>
            <div className="skill-view-title">
              <h3>{viewingSkill.name}</h3>
              <span className={`skill-badge ${viewingSkill.source}`}>{viewingSkill.source}</span>
            </div>
            {viewingSkill.description && (
              <div className="skill-description" style={{ marginTop: 8, color: 'var(--color-text-secondary)', fontSize: '0.875rem' }}>
                {viewingSkill.description}
              </div>
            )}
            <div className="skill-meta" style={{ marginTop: 8, fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
              Path: {viewingSkill.path}
            </div>
          </div>
          <div className="skill-content">{viewingSkill.content}</div>
        </div>
      </div>
    )
  }

  return (
    <div className={`skills-panel ${embedded ? 'embedded' : ''}`}>
      {!embedded && (
        <div className="skills-header">
          <h2>Skills</h2>
          <div className="skills-header-actions">
            <label className="btn-new-skill">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
              {uploading ? 'Uploading...' : 'Upload Skill'}
              <input ref={zipInputRef} type="file" accept=".zip" style={{ display: 'none' }} onChange={handleFileSelect} disabled={uploading} />
            </label>
            <button className="btn-close-skills-inline" onClick={onClose} type="button" aria-label="Close">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
            </button>
          </div>
        </div>
      )}

      {embedded && (
        <div className="skills-upload-area">
          <label className="skills-upload-btn">
            Upload Skill (ZIP)
            <input ref={zipInputRef} type="file" accept=".zip" style={{ display: 'none' }} onChange={handleFileSelect} disabled={uploading} />
          </label>
        </div>
      )}

      <div className="skill-tabs">
        <button className={`skill-tab ${tab === 'personal' ? 'active' : ''}`} onClick={() => setTab('personal')} type="button">Personal</button>
        <button className={`skill-tab ${tab === 'shared' ? 'active' : ''}`} onClick={() => setTab('shared')} type="button">Shared</button>
      </div>

      {error && <div className="skills-error">{error}</div>}

      {loading ? (
        <div className="skills-loading">Loading skills...</div>
      ) : skills.length === 0 ? (
        <div className="skills-empty">
          {isPersonal ? 'No personal skills yet.' : 'No shared skills available.'}
        </div>
      ) : (
        <SkillList skills={skills} isPersonal={isPersonal} onDelete={handleDelete} onView={setViewingSkill} onPromote={handlePromote} promoting={promoting} />
      )}
    </div>
  )
}

// ── Skill List ───────────────────────────────────────────────────

interface SkillListProps {
  skills: Skill[]
  isPersonal: boolean
  onDelete: (name: string) => void
  onView: (skill: Skill) => void
  onPromote: (name: string) => void
  promoting: string | null
}

function SkillList({ skills, isPersonal, onDelete, onView, onPromote, promoting }: SkillListProps) {
  return (
    <div className="skill-list">
      {skills.map((skill) => (
        <div key={skill.name} className="skill-row">
          <div className="skill-header">
            <span className="skill-icon">📦</span>
            <span className="skill-name">{skill.name}</span>
            <span className={`skill-badge ${skill.source}`}>{skill.source}</span>
          </div>
          <div className="skill-meta">Created: {skill.created_at ? new Date(skill.created_at).toLocaleDateString() : 'N/A'}</div>
          <div className="skill-actions">
            <button className="skill-view-btn" onClick={() => onView(skill)} type="button">View</button>
            {isPersonal && (
              <button className="skill-promote-btn" onClick={() => onPromote(skill.name)} type="button" disabled={promoting === skill.name}>
                {promoting === skill.name ? 'Promoting...' : 'Promote'}
              </button>
            )}
            <button className="skill-delete-btn" onClick={() => onDelete(skill.name)} type="button">Delete</button>
          </div>
        </div>
      ))}
    </div>
  )
}

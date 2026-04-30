import { useState, useCallback, useEffect, useRef } from 'react'
import type { Skill } from '../lib/types'
import { useSkillsApi } from '../hooks/useSkillsApi'

interface SkillsPageProps {
  authToken: string | null
  userId: string
  onBack: () => void
}

type Tab = 'shared' | 'personal'

export default function SkillsPage({ authToken, userId, onBack }: SkillsPageProps) {
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

  if (viewingSkill) {
    return (
      <div className="skills-page feedback-page">
        <div className="skills-header feedback-header">
          <button className="skills-back-btn feedback-back-btn" onClick={() => setViewingSkill(null)} type="button">&larr; Back</button>
          <div className="skills-header-title-group">
            <h2>{viewingSkill.name}</h2>
            <span className={`skill-badge ${viewingSkill.source}`}>{viewingSkill.source}</span>
          </div>
        </div>
        {viewingSkill.description && (
          <p className="skill-description">{viewingSkill.description}</p>
        )}
        <div className="skill-meta-line">Path: {viewingSkill.path}</div>
        <div className="skill-content">{viewingSkill.content}</div>
      </div>
    )
  }

  return (
    <div className="skills-page feedback-page">
      <div className="skills-header feedback-header">
        <button className="skills-back-btn feedback-back-btn" onClick={onBack} type="button">&larr; Back</button>
        <div className="skills-header-title-group">
          <label className="skills-upload-btn">
            {uploading ? 'Uploading...' : '+ Upload Skill'}
            <input ref={zipInputRef} type="file" accept=".zip" style={{ display: 'none' }} onChange={handleFileSelect} disabled={uploading} />
          </label>
          <h2>Skills Management</h2>
        </div>
      </div>

      <div className="skills-tabs">
        <button className={`skills-tab ${tab === 'personal' ? 'active' : ''}`} onClick={() => setTab('personal')} type="button">Personal</button>
        <button className={`skills-tab ${tab === 'shared' ? 'active' : ''}`} onClick={() => setTab('shared')} type="button">Shared</button>
      </div>

      {error && <div className="skills-error">{error}</div>}

      {loading ? (
        <div className="skills-loading">Loading skills...</div>
      ) : skills.length === 0 ? (
        <div className="skills-empty">
          {isPersonal ? 'No personal skills yet.' : 'No shared skills available.'}
        </div>
      ) : (
        <div className="skill-list">
          {skills.map((skill) => (
            <div key={skill.name} className={`skill-row${skill.valid ? '' : ' skill-invalid'}`}>
              <div className="skill-header">
                <span className="skill-icon">{skill.valid ? '\ud83d\udce6' : '\u26a0\ufe0f'}</span>
                <span className="skill-name">{skill.name}</span>
                <span className={`skill-badge ${skill.source}`}>{skill.source}</span>
                {!skill.valid && <span className="skill-badge invalid">invalid</span>}
              </div>
              <div className="skill-meta">{skill.description}</div>
              <div className="skill-actions">
                {skill.valid && (
                  <button className="skill-view-btn" onClick={() => setViewingSkill(skill)} type="button">View</button>
                )}
                {isPersonal && skill.valid && (
                  <button className="skill-promote-btn" onClick={() => handlePromote(skill.name)} type="button" disabled={promoting === skill.name}>
                    {promoting === skill.name ? 'Promoting...' : 'Promote'}
                  </button>
                )}
                <button className="skill-delete-btn" onClick={() => handleDelete(skill.name)} type="button">Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

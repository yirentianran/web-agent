import { useState, useCallback, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import type { Skill } from '../lib/types'
import { useSkillsApi } from '../hooks/useSkillsApi'

interface SkillsPageProps {
  authToken: string | null
  userId: string
  userRole: string
  onBack: () => void
}

type Tab = 'shared' | 'personal'

export default function SkillsPage({ authToken, userId, userRole, onBack }: SkillsPageProps) {
  const { t } = useTranslation()
  const api = useSkillsApi(authToken, userId)
  const isAdmin = userRole === 'admin'
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
      setError(e instanceof Error ? e.message : t('skills.loadFailed'))
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
      setError(e instanceof Error ? e.message : t('skills.uploadFailed'))
    } finally {
      setUploading(false)
    }
  }, [api, fetchSkills, tab])

  const handleDelete = useCallback(async (name: string) => {
    if (!confirm(t('skills.confirmDelete', { name }))) return
    try {
      if (tab === 'shared') {
        await api.deleteShared(name)
      } else {
        await api.delete(name)
      }
      await fetchSkills()
      setViewingSkill(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : t('skills.deleteFailed'))
    }
  }, [api, fetchSkills, tab])

  const handlePromote = useCallback(async (name: string) => {
    if (!confirm(t('skills.confirmPromote', { name }))) return
    setPromoting(name)
    try {
      await api.promote(name)
      await fetchSkills()
    } catch (e) {
      setError(e instanceof Error ? e.message : t('skills.promoteFailed'))
    } finally {
      setPromoting(null)
    }
  }, [api, fetchSkills])

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) handleUpload(f)
    e.target.value = ''
  }

  const handleDownload = useCallback(async (skill: Skill) => {
    try {
      const owner = skill.source === 'personal'
        ? (skill.owner || userId)
        : skill.owner
      await api.downloadSkill(skill.source, skill.name, owner)
    } catch (e) {
      setError(e instanceof Error ? e.message : t('skills.downloadFailed'))
    }
  }, [api, t])

  const skills = tab === 'shared' ? sharedSkills : personalSkills
  const isPersonal = tab === 'personal'

  if (viewingSkill) {
    return (
      <div className="skills-page feedback-page">
        <div className="skills-header feedback-header">
          <button className="skills-back-btn feedback-back-btn" onClick={() => setViewingSkill(null)} type="button">{t('common.back')}</button>
          <div className="skills-header-title-group">
            <h2>{viewingSkill.name}</h2>
            <span className={`skill-badge ${viewingSkill.source}`}>{viewingSkill.source}</span>
          </div>
        </div>
        {viewingSkill.description && (
          <p className="skill-description">{viewingSkill.description}</p>
        )}
        <div className="skill-meta-line">{t('skills.path', { path: viewingSkill.path })}</div>
        <div className="skill-content">{viewingSkill.content}</div>
      </div>
    )
  }

  return (
    <div className="skills-page feedback-page">
      <div className="skills-header feedback-header">
        <button className="skills-back-btn feedback-back-btn" onClick={onBack} type="button">{t('common.back')}</button>
        <div className="skills-header-title-group">
          <label className="skills-upload-btn">
            {uploading ? t('skills.uploadingSkill') : t('skills.uploadSkill')}
            <input ref={zipInputRef} type="file" accept=".zip" style={{ display: 'none' }} onChange={handleFileSelect} disabled={uploading} />
          </label>
          <h2>{t('skills.title')}</h2>
        </div>
      </div>

      <div className="skills-tabs">
        <button className={`skills-tab ${tab === 'personal' ? 'active' : ''}`} onClick={() => setTab('personal')} type="button">{t('skills.personalTab')}</button>
        {isAdmin && (
          <button className={`skills-tab ${tab === 'shared' ? 'active' : ''}`} onClick={() => setTab('shared')} type="button">{t('skills.sharedTab')}</button>
        )}
      </div>

      {error && <div className="skills-error">{error}</div>}

      {loading ? (
        <div className="skills-loading">{t('skills.loading')}</div>
      ) : skills.length === 0 ? (
        <div className="skills-empty">
          {isPersonal ? t('skills.noPersonal') : t('skills.noShared')}
        </div>
      ) : (
        <div className="skill-list">
          {skills.map((skill) => (
            <div key={skill.name} className={`skill-row${skill.valid ? '' : ' skill-invalid'}`}>
              <div className="skill-header">
                <span className="skill-icon">{skill.valid ? '\ud83d\udce6' : '\u26a0\ufe0f'}</span>
                <span className="skill-name">{skill.name}</span>
                <span className={`skill-badge ${skill.source}`}>{skill.source}</span>
                {!skill.valid && <span className="skill-badge invalid">{t('skills.invalid')}</span>}
              </div>
              <div className="skill-meta">{skill.description}</div>
              <div className="skill-actions">
                <button className="skill-download-btn" onClick={() => handleDownload(skill)} type="button">{t('skills.download')}</button>
                {skill.valid && (
                  <button className="skill-view-btn" onClick={() => setViewingSkill(skill)} type="button">{t('common.view')}</button>
                )}
                {isPersonal && skill.valid && (
                  <button className="skill-promote-btn" onClick={() => handlePromote(skill.name)} type="button" disabled={promoting === skill.name}>
                    {promoting === skill.name ? t('common.promoting') : t('common.promote')}
                  </button>
                )}
                <button className="skill-delete-btn" onClick={() => handleDelete(skill.name)} type="button">{t('common.delete')}</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useSkillEvolutionApi } from '../hooks/useSkillEvolutionApi'

type Tab = 'candidates' | 'versions' | 'review'

interface EvolutionPanelProps {
  userId: string
  authToken: string | null
  onBack: () => void
}

interface Candidate {
  skill_name: string
  count: number
  average_rating: number
  high_quality_count: number
}

interface FileEntry {
  path: string
  size: number
  is_skill_md: boolean
}

export default function EvolutionPanel({ userId: _userId, authToken, onBack }: EvolutionPanelProps) {
  const { t } = useTranslation()
  const api = useSkillEvolutionApi(authToken)
  const [tab, setTab] = useState<Tab>('candidates')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null)
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [versions, setVersions] = useState<string[]>([])
  const [previewContent, setPreviewContent] = useState<string | null>(null)
  const [previewVersion, setPreviewVersion] = useState<number | null>(null)
  const [previewFiles, setPreviewFiles] = useState<FileEntry[]>([])
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [selectedFileContent, setSelectedFileContent] = useState<string | null>(null)
  const [evolving, setEvolving] = useState<string | null>(null)
  const [evolveTaskId, setEvolveTaskId] = useState<string | null>(null)
  const [evolvePolling, setEvolvePolling] = useState(false)

  // Load candidates on mount
  useEffect(() => {
    loadCandidates()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Poll evolve status when a task is running
  useEffect(() => {
    if (!evolvePolling || !evolveTaskId || !selectedSkill) return

    const interval = setInterval(async () => {
      try {
        const status = await api.getEvolveStatus(selectedSkill, evolveTaskId)
        if (status.status === 'complete') {
          setEvolvePolling(false)
          setEvolving(null)
          // Fetch the version files
          const filesResp = await api.getVersionFiles(selectedSkill, previewVersion!)
          setPreviewFiles(filesResp.files)
          // Load SKILL.md content if available
          const skillMd = filesResp.files.find(f => f.is_skill_md)
          if (skillMd) {
            const content = await api.getVersionFileContent(selectedSkill, previewVersion!, skillMd.path)
            setPreviewContent(typeof content === 'string' ? content : JSON.stringify(content))
          }
          setMessage(`Agent evolution complete for ${selectedSkill} (version ${previewVersion})`)
        } else if (status.status === 'failed') {
          setEvolvePolling(false)
          setEvolving(null)
          setError(`Agent evolution failed for ${selectedSkill}`)
        }
      } catch {
        // Polling error — just continue
      }
    }, 3000)

    return () => clearInterval(interval)
  }, [evolvePolling, evolveTaskId, selectedSkill, previewVersion, api])

  async function loadCandidates() {
    setLoading(true)
    setError(null)
    try {
      const resp = await api.listEvolutionCandidates()
      setCandidates(resp.candidates)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('evolution.loadFailed'))
    } finally {
      setLoading(false)
    }
  }

  async function handlePreview(skillName: string) {
    setEvolving(skillName)
    setError(null)
    setMessage(null)
    try {
      const resp = await api.evolveAgent(skillName)
      if (resp.status === 'ok') {
        const ver = resp.version_number
        setPreviewVersion(ver)
        setSelectedSkill(skillName)
        setEvolveTaskId(resp.task_id)
        setEvolvePolling(true)
        setTab('review')
        setMessage(`Agent evolution started for ${skillName} (version ${ver})`)
      } else {
        setError(resp.message ?? t('evolution.previewFailed'))
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('evolution.previewFailed'))
      setEvolving(null)
    }
  }

  async function handleActivate(skillName: string) {
    if (previewVersion === null) return
    setError(null)
    setMessage(null)
    try {
      const resp = await api.activateVersion(skillName, previewVersion)
      if (resp.status === 'ok') {
        setMessage(`Version ${resp.version_number} of ${skillName} activated successfully`)
        setSelectedSkill(null)
        setPreviewVersion(null)
        setPreviewContent(null)
        setPreviewFiles([])
        setSelectedFile(null)
        setSelectedFileContent(null)
        setTab('candidates')
        loadCandidates()
      } else {
        setError(t('evolution.activationFailed'))
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('evolution.activationFailed'))
    }
  }

  async function handleRollback(skillName: string) {
    setError(null)
    setMessage(null)
    try {
      const resp = await api.rollbackVersion(skillName)
      if (resp.status === 'ok') {
        setMessage(`Rolled back ${skillName} to version ${resp.restored_version}`)
        setSelectedSkill(null)
        setPreviewVersion(null)
        setPreviewContent(null)
        setPreviewFiles([])
        setSelectedFile(null)
        setSelectedFileContent(null)
      } else if (resp.status === 'info') {
        setMessage(resp.message ?? 'No action needed')
      } else {
        setError(`${t('common.rollback')} failed: ${resp.reason ?? 'unknown reason'}`)
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('evolution.rollbackFailed'))
    }
  }

  const handleLoadVersions = async (skillName: string) => {
    setLoading(true)
    setSelectedSkill(skillName)
    setError(null)
    try {
      const resp = await api.listVersions(skillName)
      setVersions(resp.versions)
      setTab('versions')
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('evolution.loadFailed'))
    } finally {
      setLoading(false)
    }
  }

  async function handleFileSelect(skillName: string, versionNumber: number, filePath: string) {
    setSelectedFile(filePath)
    try {
      const content = await api.getVersionFileContent(skillName, versionNumber, filePath)
      setSelectedFileContent(typeof content === 'string' ? content : JSON.stringify(content, null, 2))
    } catch {
      setSelectedFileContent(t('evolution.loadFileFailed'))
    }
  }

  return (
    <div className="evolution-panel feedback-page">
      <div className="evolution-panel-header feedback-header">
        <button className="evolution-back-btn feedback-back-btn" onClick={onBack}>&larr; {t('common.back')}</button>
        <h2>{t('evolution.title')}</h2>
      </div>

      <div className="evolution-tabs">
        {(['candidates', 'versions', 'review'] as Tab[]).map(tabVal => (
          <button
            key={tabVal}
            className={`evolution-tab ${tab === tabVal ? 'active' : ''}`}
            onClick={() => {
              setError(null)
              if (tabVal === 'versions' && !selectedSkill && candidates.length > 0) {
                handleLoadVersions(candidates[0].skill_name)
              } else {
                setTab(tabVal)
              }
            }}
          >
            {tabVal === 'candidates' ? t('evolution.candidatesTab') : tabVal === 'versions' ? t('evolution.versionsTab') : t('evolution.reviewTab')}
          </button>
        ))}
      </div>

      {error && <div className="evolution-error">{error}</div>}
      {message && <div className="evolution-message">{message}</div>}

      {loading && <div className="evolution-loading">{t('common.loading')}</div>}

      {!error && tab === 'candidates' && (
        <div className="evolution-candidates">
          {candidates.length === 0 ? (
            <p>{t('evolution.empty')}</p>
          ) : (
            <table className="evolution-table">
              <thead>
                <tr>
                  <th>{t('evolution.skill')}</th>
                  <th>{t('evolution.feedbackCount')}</th>
                  <th>{t('evolution.avgRating')}</th>
                  <th>{t('evolution.highQuality')}</th>
                  <th>{t('evolution.actions')}</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map(c => (
                  <tr key={c.skill_name}>
                    <td><strong>{c.skill_name}</strong></td>
                    <td>{c.count}</td>
                    <td>{c.average_rating.toFixed(2)}</td>
                    <td>{c.high_quality_count}</td>
                    <td>
                      <button
                        className="btn-preview"
                        onClick={() => handlePreview(c.skill_name)}
                        disabled={evolving === c.skill_name}
                      >
                        {evolving === c.skill_name ? t('common.evolving') : t('common.preview')}
                      </button>
                      <button className="btn-versions" onClick={() => handleLoadVersions(c.skill_name)}>
                        Versions
                      </button>
                      <button className="btn-rollback" onClick={() => handleRollback(c.skill_name)}>
                        {t('common.rollback')}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {tab === 'versions' && (
        <div className="evolution-versions">
          {!selectedSkill && (
            <p>{t('evolution.selectSkill')}</p>
          )}
          {selectedSkill && (
            <>
              <h2>{t('evolution.versionsOf', { skill: selectedSkill })}</h2>
              {versions.length === 0 ? (
                <p>{t('evolution.noVersions')}</p>
              ) : (
                <ul>
                  {versions.map(v => {
                    const num = v === 'current' ? NaN : parseInt(v.replace('SKILL_v', ''), 10)
                    return (
                      <li key={v}>
                        <span>
                          {v}
                        </span>
                        {!isNaN(num) && (
                          <button
                            className="btn-activate"
                            onClick={async () => {
                              setPreviewVersion(num)
                              setSelectedSkill(selectedSkill)
                              // Load version content for old-format SKILL_v{N}.md
                              try {
                                const contentResp = await api.getVersionContent(selectedSkill, v)
                                setPreviewContent(typeof contentResp === 'string' ? contentResp : contentResp.content ?? JSON.stringify(contentResp))
                              } catch {
                                setPreviewContent(t('evolution.loadVersionFailed'))
                              }
                              setTab('review')
                            }}
                          >
                            {t('common.activate')}
                          </button>
                        )}
                      </li>
                    )
                  })}
                </ul>
              )}
            </>
          )}
        </div>
      )}

      {tab === 'review' && (
        <div className="evolution-review">
          {!selectedSkill && (
            <p>Preview a skill from the Candidates tab to review it here.</p>
          )}
          {selectedSkill && (
            <>
              <h2>{t('evolution.reviewOf', { skill: selectedSkill })}</h2>
              {previewVersion !== null && (
                <p className="review-version-info">
                  {t('evolution.pendingVersion', { version: previewVersion })}
                  {evolving === selectedSkill && ' \u00b7 Evolving...'}
                </p>
              )}

              {previewFiles.length > 0 && (
                <div className="review-file-tree">
                  <h3>{t('evolution.generatedFiles')}</h3>
                  <ul className="file-tree-list">
                    {previewFiles.map(f => (
                      <li
                        key={f.path}
                        className={`file-tree-item ${selectedFile === f.path ? 'selected' : ''}`}
                        onClick={() => handleFileSelect(selectedSkill, previewVersion!, f.path)}
                      >
                        <span className="file-icon">{f.is_skill_md ? '\u{1f4c4}' : '\u{1f4c1}'}</span>
                        <span className="file-path">{f.path}</span>
                        <span className="file-size">{f.size < 1024 ? `${f.size}B` : `${(f.size / 1024).toFixed(1)}KB`}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {selectedFileContent && (
                <div className="review-content-preview">
                  <h3>{selectedFile}</h3>
                  <pre className="review-content-code">{selectedFileContent}</pre>
                </div>
              )}

              {!selectedFileContent && previewContent && previewFiles.length === 0 && (
                <div className="review-content-preview">
                  <h3>{t('evolution.generatedContent')}</h3>
                  <pre className="review-content-code">{previewContent}</pre>
                </div>
              )}

              {!selectedFileContent && !previewContent && previewFiles.length === 0 && evolving !== selectedSkill && (
                <p className="review-no-content">{t('evolution.noContent')}</p>
              )}

              <div className="review-actions">
                <button
                  className="btn-activate"
                  onClick={() => handleActivate(selectedSkill)}
                  disabled={previewVersion === null || evolving === selectedSkill}
                >
                  {t('common.activate')}
                </button>
                <button className="btn-rollback" onClick={() => handleRollback(selectedSkill)}>
                  {t('common.rollback')}
                </button>
                <button className="btn-cancel" onClick={() => {
                  setSelectedSkill(null)
                  setPreviewVersion(null)
                  setPreviewContent(null)
                  setPreviewFiles([])
                  setSelectedFile(null)
                  setSelectedFileContent(null)
                  setEvolveTaskId(null)
                  setEvolvePolling(false)
                  setTab('candidates')
                }}>
                  {t('common.cancel')}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

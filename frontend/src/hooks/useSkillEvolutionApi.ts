import { useMemo, useRef, useCallback } from 'react'

export interface EvolutionCandidate {
  skill_name: string
  count: number
  average_rating: number
  high_quality_count: number
}

export interface PreviewResult {
  status: string
  version_number?: number
  activated: boolean
  message?: string
  reason?: string
}

export interface ActivateResult {
  status: string
  activated: boolean
  version_number?: number
  backup?: string
}

export interface RollbackResult {
  status: string
  rolled_back: boolean
  restored_version?: string
  reason?: string
  message?: string
}

export interface VersionInfo {
  skill_name: string
  versions: string[]
  feedback_stats: {
    count: number
    average_rating: number
    high_quality_count: number
  }
}

export interface VersionContent {
  content: string
  name: string
}

export interface EvolveAgentResult {
  status: string
  task_id: string
  version_number: number
  version_path: string
  message: string
}

export interface EvolveStatusResult {
  status: string
  task_id: string
  files?: { path: string; size: number }[]
  messages?: unknown[]
}

export interface VersionFileInfo {
  path: string
  size: number
  is_skill_md: boolean
}

export interface VersionFilesResult {
  status: string
  version: number
  files: VersionFileInfo[]
}

export function useSkillEvolutionApi(authToken: string | null) {
  const headersRef = useRef<HeadersInit>({})
  headersRef.current = authToken ? { Authorization: `Bearer ${authToken}` } : {}

  const fetchJSON = useCallback(async (url: string, options?: RequestInit) => {
    const resp = await fetch(url, {
      ...options,
      headers: { ...headersRef.current, 'Content-Type': 'application/json', ...options?.headers },
    })
    if (!resp.ok) {
      let detail = resp.statusText
      try {
        const body = await resp.json()
        detail = body.detail ?? detail
      } catch {
        // response body not JSON, fall back to statusText
      }
      throw new Error(`HTTP ${resp.status}: ${detail}`)
    }
    return resp.json()
  }, [])

  return useMemo(
    () => ({
      listEvolutionCandidates: (): Promise<{ candidates: EvolutionCandidate[] }> =>
        fetchJSON('/api/admin/skills/evolution-candidates'),

      previewEvolution: (skillName: string, model: string = 'claude-sonnet-4-6'): Promise<PreviewResult> =>
        fetchJSON(`/api/skills/${encodeURIComponent(skillName)}/evolve`, {
          method: 'POST',
          body: JSON.stringify({ model }),
        }),

      activateVersion: (
        skillName: string,
        versionNumber: number,
      ): Promise<ActivateResult> =>
        fetchJSON(`/api/skills/${encodeURIComponent(skillName)}/activate-version`, {
          method: 'POST',
          body: JSON.stringify({ version_number: versionNumber }),
        }),

      rollbackVersion: (skillName: string): Promise<RollbackResult> =>
        fetchJSON(`/api/skills/${encodeURIComponent(skillName)}/rollback`, {
          method: 'POST',
        }),

      listVersions: (skillName: string): Promise<VersionInfo> =>
        fetchJSON(`/api/skills/${encodeURIComponent(skillName)}/version`),

      getVersionContent: (skillName: string, versionName: string): Promise<VersionContent> =>
        fetchJSON(`/api/skills/${encodeURIComponent(skillName)}/version/${encodeURIComponent(versionName)}`),

      evolveAgent: (skillName: string, model: string = 'claude-sonnet-4-6'): Promise<EvolveAgentResult> =>
        fetchJSON(`/api/skills/${encodeURIComponent(skillName)}/evolve-agent`, {
          method: 'POST',
          body: JSON.stringify({ model }),
        }),

      getEvolveStatus: (skillName: string, taskId: string): Promise<EvolveStatusResult> =>
        fetchJSON(`/api/skills/${encodeURIComponent(skillName)}/evolve-status/${encodeURIComponent(taskId)}`),

      getVersionFiles: (skillName: string, versionNumber: number): Promise<VersionFilesResult> =>
        fetchJSON(`/api/skills/${encodeURIComponent(skillName)}/version-files/${encodeURIComponent(String(versionNumber))}`),

      getVersionFileContent: (skillName: string, versionNumber: number, filePath: string): Promise<string> =>
        fetchJSON(`/api/skills/${encodeURIComponent(skillName)}/version-file/${encodeURIComponent(String(versionNumber))}?file_path=${encodeURIComponent(filePath)}`).then(
          (resp) => resp.content ?? resp,
        ),
    }),
    [fetchJSON],
  )
}

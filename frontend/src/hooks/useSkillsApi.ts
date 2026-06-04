import { useMemo, useRef, useCallback } from 'react'
import type { Skill } from '../lib/types'
import { csrfHeaders } from '../lib/api'

export function useSkillsApi(userId: string) {
  const headersRef = useRef<HeadersInit>({})
  headersRef.current = csrfHeaders()

  const fetchJSON = useCallback(async (url: string, options?: RequestInit) => {
    const resp = await fetch(url, { ...options, headers: { ...headersRef.current, 'Content-Type': 'application/json', ...options?.headers } })
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

  return useMemo(() => ({
    listShared: (): Promise<Skill[]> => fetchJSON('/api/shared-skills'),
    listPersonal: (): Promise<Skill[]> => fetchJSON(`/api/users/${userId}/skills`),
    delete: (name: string): Promise<{ status: string }> =>
      fetchJSON(`/api/users/${userId}/skills/${encodeURIComponent(name)}`, { method: 'DELETE' }),
    deleteShared: (name: string): Promise<{ status: string }> =>
      fetchJSON(`/api/shared-skills/${encodeURIComponent(name)}`, { method: 'DELETE' }),
    uploadPersonal: async (file: File): Promise<{ status: string; skill_name: string; files: string[] }> => {
      const formData = new FormData()
      formData.append('file', file)
      const resp = await fetch(`/api/users/${userId}/skills/upload`, {
        method: 'POST',
        body: formData,
        headers: { ...headersRef.current },
      })
      return handleUploadResponse(resp)
    },
    uploadShared: async (file: File): Promise<{ status: string; skill_name: string; files: string[] }> => {
      const formData = new FormData()
      formData.append('file', file)
      const resp = await fetch('/api/shared-skills/upload', {
        method: 'POST',
        body: formData,
        headers: { ...headersRef.current },
      })
      return handleUploadResponse(resp)
    },
    promote: (name: string, owner: string): Promise<{ status: string; skill_name: string; message: string }> =>
      fetchJSON(`/api/users/${encodeURIComponent(owner)}/skills/${encodeURIComponent(name)}/promote`, { method: 'POST' }),
    downloadSkill: async (source: 'shared' | 'personal', skillName: string, owner?: string): Promise<void> => {
      const params = new URLSearchParams()
      if (owner) params.set('owner', owner)

      const url = `/api/skills/download/${source}/${encodeURIComponent(skillName)}?${params}`

      const response = await fetch(url, {
        headers: { ...headersRef.current },
      })

      if (!response.ok) {
        const error = await response.json().catch(() => ({ error: 'Download failed' }))
        throw new Error(error.error || 'Download failed')
      }

      const blob = await response.blob()
      const downloadUrl = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = downloadUrl
      a.download = `${skillName}.zip`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      window.URL.revokeObjectURL(downloadUrl)
    },
  }), [userId, fetchJSON])
}

async function handleUploadResponse(resp: Response): Promise<{ status: string; skill_name: string; files: string[] }> {
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      const body = await resp.json()
      detail = body.detail ?? detail
    } catch { /* not JSON */ }
    throw new Error(`HTTP ${resp.status}: ${detail}`)
  }
  return resp.json()
}

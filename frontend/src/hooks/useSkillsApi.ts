import { useMemo, useRef, useCallback } from 'react'
import type { Skill } from '../lib/types'

export function useSkillsApi(authToken: string | null, userId: string) {
  const headersRef = useRef<HeadersInit>({})
  headersRef.current = authToken ? { Authorization: `Bearer ${authToken}` } : {}

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
      const resp = await fetch(`/api/users/${userId}/skills/upload`, { method: 'POST', body: formData })
      return handleUploadResponse(resp)
    },
    uploadShared: async (file: File): Promise<{ status: string; skill_name: string; files: string[] }> => {
      const formData = new FormData()
      formData.append('file', file)
      const resp = await fetch('/api/shared-skills/upload', { method: 'POST', body: formData })
      return handleUploadResponse(resp)
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

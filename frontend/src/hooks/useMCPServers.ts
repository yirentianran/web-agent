import { useMemo, useRef, useCallback } from 'react'
import type { McpServer } from '../lib/types'

export function useMCPServers(authToken: string | null) {
  const headersRef = useRef<HeadersInit>({})
  headersRef.current = authToken ? { Authorization: `Bearer ${authToken}` } : {}

  const fetchJSON = useCallback(async (url: string, options?: RequestInit) => {
    const resp = await fetch(url, {
      ...options,
      headers: { ...headersRef.current, 'Content-Type': 'application/json', ...options?.headers }
    })
    if (!resp.ok) {
      let detail = resp.statusText
      try {
        const body = await resp.json()
        detail = body.detail ?? detail
      } catch {
        // response body not JSON
      }
      throw new Error(`HTTP ${resp.status}: ${detail}`)
    }
    return resp.json()
  }, [])

  const baseUrl = '/api/admin/mcp-servers'

  return useMemo(() => ({
    listServers: (): Promise<McpServer[]> => fetchJSON(baseUrl),
    createServer: (server: Omit<McpServer, 'enabled'> & { enabled?: boolean }): Promise<{ status: string }> =>
      fetchJSON(baseUrl, { method: 'POST', body: JSON.stringify(server) }),
    updateServer: (name: string, server: McpServer): Promise<{ status: string }> =>
      fetchJSON(`${baseUrl}/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify(server) }),
    deleteServer: (name: string): Promise<{ status: string }> =>
      fetchJSON(`${baseUrl}/${encodeURIComponent(name)}`, { method: 'DELETE' }),
    toggleServer: (name: string, enabled: boolean): Promise<{ status: string }> =>
      fetchJSON(`${baseUrl}/${encodeURIComponent(name)}/toggle?enabled=${enabled}`, { method: 'PATCH' }),
  }), [fetchJSON, baseUrl])
}

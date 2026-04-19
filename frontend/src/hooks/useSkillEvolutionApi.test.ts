import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useSkillEvolutionApi } from './useSkillEvolutionApi'

const mockFetch = vi.fn()

beforeEach(() => {
  mockFetch.mockReset()
  global.fetch = mockFetch
})

afterEach(() => {
  vi.restoreAllMocks()
})

function renderApi(authToken: string | null = 'test-token') {
  return renderHook(() => useSkillEvolutionApi(authToken))
}

describe('useSkillEvolutionApi', () => {
  describe('listEvolutionCandidates', () => {
    it('fetches evolution candidates', async () => {
      const candidates = {
        candidates: [
          { skill_name: 'bad-skill', count: 15, average_rating: 2.3, high_quality_count: 1 },
        ],
      }
      mockFetch.mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(candidates) })

      const { result } = renderApi()

      let data: Awaited<ReturnType<typeof result.current.listEvolutionCandidates>> | undefined
      await act(async () => {
        data = await result.current.listEvolutionCandidates()
      })

      expect(data).toEqual(candidates)
      expect(mockFetch).toHaveBeenCalledWith('/api/admin/skills/evolution-candidates', expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }))
    })
  })

  describe('previewEvolution', () => {
    it('POSTs to evolve endpoint', async () => {
      const response = { status: 'ok', version_number: 1, activated: false, message: 'Preview generated' }
      mockFetch.mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(response) })

      const { result } = renderApi()

      let data: Awaited<ReturnType<typeof result.current.previewEvolution>> | undefined
      await act(async () => {
        data = await result.current.previewEvolution('test-skill', 'claude-sonnet-4-6')
      })

      expect(data).toEqual(response)
      expect(mockFetch).toHaveBeenCalledWith('/api/skills/test-skill/evolve', expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ model: 'claude-sonnet-4-6' }),
      }))
    })
  })

  describe('activateVersion', () => {
    it('POSTs to activate-version endpoint', async () => {
      const response = { status: 'ok', activated: true, version_number: 1, backup: '/path/to/backup' }
      mockFetch.mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(response) })

      const { result } = renderApi()

      let data: Awaited<ReturnType<typeof result.current.activateVersion>> | undefined
      await act(async () => {
        data = await result.current.activateVersion('test-skill', 1)
      })

      expect(data).toEqual(response)
      expect(mockFetch).toHaveBeenCalledWith('/api/skills/test-skill/activate-version', expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ version_number: 1 }),
      }))
    })
  })

  describe('rollbackVersion', () => {
    it('POSTs to rollback endpoint', async () => {
      const response = { status: 'ok', rolled_back: true, restored_version: 'v1' }
      mockFetch.mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(response) })

      const { result } = renderApi()

      let data: Awaited<ReturnType<typeof result.current.rollbackVersion>> | undefined
      await act(async () => {
        data = await result.current.rollbackVersion('test-skill')
      })

      expect(data).toEqual(response)
      expect(mockFetch).toHaveBeenCalledWith('/api/skills/test-skill/rollback', expect.objectContaining({
        method: 'POST',
      }))
    })
  })

  describe('listVersions', () => {
    it('fetches version info', async () => {
      const response = {
        skill_name: 'test-skill',
        versions: ['current', 'SKILL_v1', 'SKILL_backup_v1'],
        feedback_stats: { count: 10, average_rating: 3.5, high_quality_count: 2 },
      }
      mockFetch.mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(response) })

      const { result } = renderApi()

      let data: Awaited<ReturnType<typeof result.current.listVersions>> | undefined
      await act(async () => {
        data = await result.current.listVersions('test-skill')
      })

      expect(data).toEqual(response)
      expect(mockFetch).toHaveBeenCalledWith('/api/skills/test-skill/version', expect.anything())
    })
  })

  describe('error handling', () => {
    it('throws on HTTP error', async () => {
      mockFetch.mockResolvedValueOnce({ ok: false, status: 401, statusText: 'Unauthorized' })

      const { result } = renderApi()

      await expect(async () => {
        await act(async () => {
          await result.current.listEvolutionCandidates()
        })
      }).rejects.toThrow('HTTP 401: Unauthorized')
    })
  })

  describe('auth token handling', () => {
    it('omits Authorization header when token is null', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ candidates: [] }) })

      const { result } = renderApi(null)

      await act(async () => {
        await result.current.listEvolutionCandidates()
      })

      const callArgs = mockFetch.mock.calls[0][1]
      expect(callArgs.headers).not.toHaveProperty('Authorization')
    })
  })
})

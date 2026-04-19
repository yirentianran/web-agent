import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import EvolutionPanel from './EvolutionPanel'

// Mock the useSkillEvolutionApi hook
const mockListCandidates = vi.fn()
const mockListVersions = vi.fn()
const mockEvolveAgent = vi.fn()
const mockActivateVersion = vi.fn()
const mockRollbackVersion = vi.fn()
const mockGetEvolveStatus = vi.fn()
const mockGetVersionFiles = vi.fn()
const mockGetVersionFileContent = vi.fn()
const mockGetVersionContent = vi.fn()

vi.mock('../hooks/useSkillEvolutionApi', () => ({
  useSkillEvolutionApi: () => ({
    listEvolutionCandidates: mockListCandidates,
    listVersions: mockListVersions,
    evolveAgent: mockEvolveAgent,
    activateVersion: mockActivateVersion,
    rollbackVersion: mockRollbackVersion,
    getEvolveStatus: mockGetEvolveStatus,
    getVersionFiles: mockGetVersionFiles,
    getVersionFileContent: mockGetVersionFileContent,
    getVersionContent: mockGetVersionContent,
  }),
}))

function renderPanel() {
  return render(
    <EvolutionPanel userId="test-user" authToken="test-token" onBack={() => {}} />
  )
}

describe('EvolutionPanel - Versions tab', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockListCandidates.mockResolvedValue({
      candidates: [
        { skill_name: 'test-skill', count: 29, average_rating: 4.31, high_quality_count: 27 },
      ],
    })
    // Backend returns versions as string[], not objects
    mockListVersions.mockResolvedValue({
      skill_name: 'test-skill',
      versions: ['SKILL_v1', 'SKILL_v2', 'current'],
      feedback_stats: { count: 29, average_rating: 4.31, high_quality_count: 27 },
    })
    mockEvolveAgent.mockResolvedValue({
      status: 'ok', task_id: 't1', version_number: 1, version_path: '/path', message: '',
    })
  })

  it('renders versions tab without crashing when backend returns string[] versions', async () => {
    renderPanel()

    // Wait for candidates to load
    await waitFor(() => {
      expect(screen.getByText('test-skill')).toBeTruthy()
    })

    // Click the Versions tab (first "Versions" button is the tab)
    fireEvent.click(screen.getAllByText('Versions')[0])

    // Should not crash — versions should be rendered
    await waitFor(() => {
      expect(screen.getByText('SKILL_v1')).toBeTruthy()
    })
    expect(screen.getByText('SKILL_v2')).toBeTruthy()
    expect(screen.getByText('current')).toBeTruthy()
  })

  it('shows "No versions found" when versions list is empty', async () => {
    mockListVersions.mockResolvedValue({
      skill_name: 'empty-skill',
      versions: [],
      feedback_stats: { count: 0, average_rating: 0, high_quality_count: 0 },
    })
    mockListCandidates.mockResolvedValue({
      candidates: [
        { skill_name: 'empty-skill', count: 0, average_rating: 0, high_quality_count: 0 },
      ],
    })

    renderPanel()

    await waitFor(() => {
      expect(screen.getByText('empty-skill')).toBeTruthy()
    })

    fireEvent.click(screen.getAllByText('Versions')[0])

    await waitFor(() => {
      expect(screen.getByText('No versions found.')).toBeTruthy()
    })
  })

  it('clears error message when switching tabs', async () => {
    mockRollbackVersion.mockResolvedValue({ status: 'failed', reason: 'No backup version found' })

    renderPanel()

    await waitFor(() => {
      expect(screen.getByText('test-skill')).toBeTruthy()
    })

    // Trigger rollback failure
    const rollbackBtn = screen.getAllByText('Rollback')[0]
    fireEvent.click(rollbackBtn)

    await waitFor(() => {
      expect(screen.getByText(/Rollback failed/i)).toBeTruthy()
    })

    // Switch to Versions tab — error should be cleared
    fireEvent.click(screen.getAllByText('Versions')[0])

    await waitFor(() => {
      expect(screen.queryByText(/Rollback failed/i)).toBeNull()
    })
  })

  it('uses feedback-page CSS classes for consistent styling', () => {
    const { container } = renderPanel()
    const panel = container.querySelector('.evolution-panel')
    expect(panel).not.toBeNull()
    expect(panel?.classList.contains('feedback-page')).toBe(true)
  })

  it('shows info message (not error) when no backup exists', async () => {
    mockRollbackVersion.mockResolvedValue({ status: 'info', message: 'No backup version found to restore' })

    renderPanel()

    await waitFor(() => {
      expect(screen.getByText('test-skill')).toBeTruthy()
    })

    const rollbackBtn = screen.getAllByText('Rollback')[0]
    fireEvent.click(rollbackBtn)

    // Should show as info message, not error
    await waitFor(() => {
      expect(screen.queryByText(/Rollback failed/i)).toBeNull()
      expect(screen.getByText(/No backup version found/i)).toBeTruthy()
    })
  })
})

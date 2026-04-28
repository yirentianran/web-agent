import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import SkillsPanel from './SkillsPanel'

// Mock the useSkillsApi hook
const mockListShared = vi.fn()
const mockListPersonal = vi.fn()
const mockDelete = vi.fn()
const mockDeleteShared = vi.fn()

vi.mock('../hooks/useSkillsApi', () => ({
  useSkillsApi: () => ({
    listShared: mockListShared,
    listPersonal: mockListPersonal,
    delete: mockDelete,
    deleteShared: mockDeleteShared,
    uploadPersonal: vi.fn(),
    uploadShared: vi.fn(),
  }),
}))

const defaultProps = {
  authToken: 'test-token',
  userId: 'test-user',
  onClose: vi.fn(),
}

function mockSkills(source: 'shared' | 'personal') {
  return [
    {
      name: 'code-review',
      source,
      description: 'Review code for quality and security',
      content: '# Code Review\n\nDetailed instructions...',
      path: `/path/to/${source}-skills/code-review`,
      created_at: '2026-04-15T00:00:00Z',
      created_by: 'upload',
      valid: true,
    },
  ]
}

describe('SkillsPanel - View skill', () => {
  it('opens skill detail view when View button is clicked', async () => {
    mockListShared.mockResolvedValue([])
    mockListPersonal.mockResolvedValue(mockSkills('personal'))

    render(<SkillsPanel {...defaultProps} />)

    await waitFor(() => {
      expect(screen.getByText('code-review')).toBeTruthy()
    })

    // Click View button
    fireEvent.click(screen.getByText('View'))

    // Should show detail view
    await waitFor(() => {
      expect(screen.getByText('Review code for quality and security')).toBeTruthy()
    })
    expect(screen.getByText(/Detailed instructions/i)).toBeTruthy()
    // Should show back button
    expect(screen.getByRole('button', { name: /back/i })).toBeTruthy()
  })

  it('shows back button to return to skill list', async () => {
    mockListShared.mockResolvedValue([])
    mockListPersonal.mockResolvedValue(mockSkills('personal'))

    render(<SkillsPanel {...defaultProps} />)

    await waitFor(() => {
      expect(screen.getByText('code-review')).toBeTruthy()
    })

    fireEvent.click(screen.getByText('View'))

    await waitFor(() => {
      expect(screen.getByText('code-review')).toBeTruthy()
    })

    // Click Back
    fireEvent.click(screen.getByRole('button', { name: /back/i }))

    // Should return to list view
    await waitFor(() => {
      expect(screen.getByText('View')).toBeTruthy()
    })
    expect(screen.queryByText(/Detailed instructions/i)).toBeNull()
  })

  it('shows skill content in pre-wrap format', async () => {
    mockListShared.mockResolvedValue([])
    mockListPersonal.mockResolvedValue(mockSkills('personal'))

    render(<SkillsPanel {...defaultProps} />)

    await waitFor(() => {
      expect(screen.getByText('code-review')).toBeTruthy()
    })

    fireEvent.click(screen.getByText('View'))

    // Content should be rendered (multi-line text, match partially)
    await waitFor(() => {
      expect(screen.getByText(/# Code Review/i)).toBeTruthy()
    })
  })
})

import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import FilesPanel from '../components/FilesPanel'

function renderFilesPanel(props?: { userId?: string; onClose?: () => void }) {
  return render(
    <FilesPanel
      userId={props?.userId ?? 'test-user'}
      onClose={props?.onClose ?? (() => {})}
    />,
  )
}

describe('FilesPanel - basic rendering', () => {
  it('renders close button', () => {
    renderFilesPanel()
    expect(screen.getByRole('button')).toBeInTheDocument()
  })

  it('renders "Files" title', () => {
    renderFilesPanel()
    expect(screen.getByText('Files')).toBeInTheDocument()
  })

  it('renders the overlay with correct class', () => {
    const { container } = renderFilesPanel()
    const overlay = container.querySelector('.files-overlay')
    expect(overlay).not.toBeNull()
  })

  it('uses sp-tabs style for header to match Settings panel', async () => {
    const originalFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    })

    const { container } = renderFilesPanel()

    await waitFor(() => {
      expect(screen.getByText(/no generated files/i)).toBeInTheDocument()
    })

    const tabsContainer = container.querySelector('.sp-tabs')
    expect(tabsContainer).not.toBeNull()

    const activeTab = container.querySelector('.sp-tab.active')
    expect(activeTab).not.toBeNull()
    expect(activeTab?.textContent).toBe('Files')

    globalThis.fetch = originalFetch
  })
})

describe('FilesPanel - loading state', () => {
  it('shows loading text while fetching files', () => {
    renderFilesPanel()
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })
})

describe('FilesPanel - empty state', () => {
  it('shows "No generated files yet" when API returns empty array', async () => {
    const originalFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    })

    renderFilesPanel()

    await waitFor(() => {
      expect(screen.getByText(/no generated files/i)).toBeInTheDocument()
    })

    globalThis.fetch = originalFetch
  })
})

describe('FilesPanel - file list rendering', () => {
  it('renders file cards when API returns files', async () => {
    const mockFiles = [
      { filename: 'report.pdf', size: 1024, generated_at: '2026-01-01T00:00:00Z', download_url: '/dl/1' },
      { filename: 'data.xlsx', size: 2048, generated_at: '2026-01-02T00:00:00Z', download_url: '/dl/2' },
    ]

    const originalFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => mockFiles,
    })

    renderFilesPanel()

    await waitFor(() => {
      expect(screen.getByText('report.pdf')).toBeInTheDocument()
      expect(screen.getByText('data.xlsx')).toBeInTheDocument()
    })

    globalThis.fetch = originalFetch
  })

  it('calls onClose when close button is clicked', () => {
    const onClose = vi.fn()
    renderFilesPanel({ onClose })

    screen.getByRole('button').click()
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

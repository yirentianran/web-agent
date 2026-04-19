import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import MCPPage from './MCPPage'

// Mock the useMCPServers hook
const mockListServers = vi.fn()

vi.mock('../hooks/useMCPServers', () => ({
  useMCPServers: () => ({
    listServers: mockListServers,
  }),
}))

function renderPage() {
  return render(
    <MCPPage userId="test-user" authToken="test-token" onBack={() => {}} />
  )
}

describe('MCPPage - styling', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockListServers.mockResolvedValue([])
  })

  it('uses feedback-page CSS class for page container', async () => {
    const { container } = renderPage()
    await waitFor(() => {
      expect(container.querySelector('.mcp-empty')).not.toBeNull()
    })
    const page = container.querySelector('.mcp-page')
    expect(page?.classList.contains('feedback-page')).toBe(true)
  })

  it('uses feedback-header CSS class for header', async () => {
    const { container } = renderPage()
    await waitFor(() => {
      expect(container.querySelector('.mcp-empty')).not.toBeNull()
    })
    const header = container.querySelector('.mcp-header')
    expect(header?.classList.contains('feedback-header')).toBe(true)
  })

  it('uses feedback-back-btn class for back button', async () => {
    const { container } = renderPage()
    await waitFor(() => {
      expect(container.querySelector('.mcp-empty')).not.toBeNull()
    })
    const backBtn = container.querySelector('.mcp-back-btn')
    expect(backBtn?.classList.contains('feedback-back-btn')).toBe(true)
  })

  it('renders title as h2 to match feedback page', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 2 })).toBeInTheDocument()
    })
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('MCP Servers')
  })

  it('places "+ Add Server" button in header next to title', async () => {
    const { container } = renderPage()
    await waitFor(() => {
      expect(container.querySelector('.mcp-empty')).not.toBeNull()
    })
    const header = container.querySelector('.mcp-header')
    const addBtn = header?.querySelector('.mcp-add-btn')
    expect(addBtn).not.toBeNull()
    expect(addBtn).toHaveTextContent('+ Add Server')
  })
})

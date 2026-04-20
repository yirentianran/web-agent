import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import MCPPage from './MCPPage'

// Mock the useMCPServers hook
const mockListServers = vi.fn()
const mockCreateServer = vi.fn()
const mockUpdateServer = vi.fn()

vi.mock('../hooks/useMCPServers', () => ({
  useMCPServers: () => ({
    listServers: mockListServers,
    createServer: mockCreateServer,
    updateServer: mockUpdateServer,
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

describe('MCPPage - JSON form', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockListServers.mockResolvedValue([])
    mockCreateServer.mockResolvedValue({ status: 'ok' })
  })

  it('opens modal with JSON textarea when clicking Add Server', async () => {
    renderPage()
    fireEvent.click(screen.getByText('+ Add Server'))
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Add MCP Server/ })).toBeInTheDocument()
    })
    // JSON textarea should be present
    const textarea = screen.getByRole('textbox', { name: /MCP Server Config/i })
    expect(textarea).toBeInTheDocument()
    expect(textarea).toHaveTextContent('{}')
  })

  it('submits valid JSON and calls createServer', async () => {
    renderPage()
    fireEvent.click(screen.getByText('+ Add Server'))
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Add MCP Server/ })).toBeInTheDocument()
    })

    const json = JSON.stringify({
      name: 'mineru',
      type: 'stdio',
      command: 'uvx',
      args: ['mineru-mcp'],
      tools: ['parse_pdf'],
      description: 'MinerU document parser',
    })
    const textarea = screen.getByRole('textbox', { name: /MCP Server Config/i })
    fireEvent.change(textarea, { target: { value: json } })
    fireEvent.click(screen.getByText('Save'))

    await waitFor(() => {
      expect(mockCreateServer).toHaveBeenCalled()
    })
    const callArg = mockCreateServer.mock.calls[0][0]
    expect(callArg.name).toBe('mineru')
    expect(callArg.type).toBe('stdio')
    expect(callArg.command).toBe('uvx')
    expect(callArg.tools).toEqual(['parse_pdf'])
  })

  it('shows error when JSON is invalid', async () => {
    renderPage()
    fireEvent.click(screen.getByText('+ Add Server'))
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Add MCP Server/ })).toBeInTheDocument()
    })

    const textarea = screen.getByRole('textbox', { name: /MCP Server Config/i })
    fireEvent.change(textarea, { target: { value: '{ invalid json' } })
    fireEvent.click(screen.getByText('Save'))

    await waitFor(() => {
      const errorEl = document.querySelector('.mcp-form-error')
      expect(errorEl).toBeInTheDocument()
      expect(errorEl?.textContent).toBe('Invalid JSON')
    })
  })

  it('shows error when name field is missing', async () => {
    renderPage()
    fireEvent.click(screen.getByText('+ Add Server'))
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Add MCP Server/ })).toBeInTheDocument()
    })

    const textarea = screen.getByRole('textbox', { name: /MCP Server Config/i })
    fireEvent.change(textarea, { target: { value: '{"type": "stdio"}' } })
    fireEvent.click(screen.getByText('Save'))

    await waitFor(() => {
      const errorEl = document.querySelector('.mcp-form-error')
      expect(errorEl).toBeInTheDocument()
      expect(errorEl?.textContent).toContain('name')
    })
  })

  it('formats JSON when clicking Format button', async () => {
    renderPage()
    fireEvent.click(screen.getByText('+ Add Server'))
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Add MCP Server/ })).toBeInTheDocument()
    })

    const textarea = screen.getByRole('textbox', { name: /MCP Server Config/i })
    fireEvent.change(textarea, { target: { value: '{"name":"test","type":"stdio"}' } })
    fireEvent.click(screen.getByText('Format'))

    expect(textarea).toHaveValue(JSON.stringify({ name: 'test', type: 'stdio' }, null, 2))
  })

  it('shows preview bar after parsing valid JSON', async () => {
    renderPage()
    fireEvent.click(screen.getByText('+ Add Server'))
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Add MCP Server/ })).toBeInTheDocument()
    })

    const json = JSON.stringify({
      name: 'mineru',
      type: 'stdio',
      command: 'uvx',
      args: ['mineru-mcp'],
      tools: ['parse_pdf', 'parse_url'],
      description: 'Parser',
    })
    const textarea = screen.getByRole('textbox', { name: /MCP Server Config/i })
    fireEvent.change(textarea, { target: { value: json } })

    await waitFor(() => {
      const preview = document.querySelector('.mcp-preview')
      expect(preview).toBeInTheDocument()
      expect(preview?.textContent).toContain('mineru')
      expect(preview?.textContent).toContain('stdio')
      expect(preview?.textContent).toContain('2 tools')
    })
  })

  it('accepts MCP config format with mcpServers wrapper', async () => {
    renderPage()
    fireEvent.click(screen.getByText('+ Add Server'))
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Add MCP Server/ })).toBeInTheDocument()
    })

    const mcpConfig = JSON.stringify({
      mcpServers: {
        mineru: {
          type: 'stdio',
          command: 'uvx',
          args: ['mineru-mcp'],
          env: {
            UV_INDEX_URL: 'https://pypi.tuna.tsinghua.edu.cn/simple',
            USE_LOCAL_API: 'true',
          },
          tools: ['parse_pdf'],
        },
      },
    })
    const textarea = screen.getByRole('textbox', { name: /MCP Server Config/i })
    fireEvent.change(textarea, { target: { value: mcpConfig } })
    fireEvent.click(screen.getByText('Save'))

    await waitFor(() => {
      expect(mockCreateServer).toHaveBeenCalled()
    })
    const callArg = mockCreateServer.mock.calls[0][0]
    expect(callArg.name).toBe('mineru')
    expect(callArg.type).toBe('stdio')
    expect(callArg.command).toBe('uvx')
    expect(callArg.tools).toEqual(['parse_pdf'])
    expect(callArg.env).toEqual({
      UV_INDEX_URL: 'https://pypi.tuna.tsinghua.edu.cn/simple',
      USE_LOCAL_API: 'true',
    })
  })
})

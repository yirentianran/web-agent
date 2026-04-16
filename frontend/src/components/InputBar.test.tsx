import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import InputBar from '../components/InputBar'

function renderInputBar(props: Partial<React.ComponentProps<typeof InputBar>> = {}) {
  const onSend = vi.fn()
  render(
    <InputBar
      onSend={onSend}
      onStop={() => {}}
      disabled={false}
      userId="test-user"
      {...props}
    />
  )
  return { onSend }
}

// ── IME composition tests (unchanged) ──────────────────────────

describe('InputBar - IME composition', () => {
  function createKeyEvent(key: string, opts?: { composing?: boolean; shiftKey?: boolean }) {
    const event = new KeyboardEvent('keydown', {
      key,
      bubbles: true,
      cancelable: true,
      shiftKey: opts?.shiftKey ?? false,
    })
    // JSDom sets isComposing to false by default; override when needed
    if (opts?.composing) {
      Object.defineProperty(event, 'isComposing', { value: true })
    }
    return event
  }

  it('sends message on Enter when not composing', () => {
    const { onSend } = renderInputBar()
    const textarea = screen.getByPlaceholderText(/Enter instruction/)

    fireEvent.change(textarea, { target: { value: 'hello' } })
    textarea.dispatchEvent(createKeyEvent('Enter'))

    expect(onSend).toHaveBeenCalledWith('hello', undefined)
  })

  it('does NOT send message on Enter during IME composition', () => {
    const { onSend } = renderInputBar()
    const textarea = screen.getByPlaceholderText(/Enter instruction/)

    fireEvent.change(textarea, { target: { value: 'nihao' } })
    textarea.dispatchEvent(createKeyEvent('Enter', { composing: true }))

    // Should NOT have sent — the Enter just commits the IME text
    expect(onSend).not.toHaveBeenCalled()
  })

  it('still allows Shift+Enter for newline even when not composing', () => {
    const { onSend } = renderInputBar()
    const textarea = screen.getByPlaceholderText(/Enter instruction/)

    fireEvent.change(textarea, { target: { value: 'hello' } })
    textarea.dispatchEvent(createKeyEvent('Enter', { shiftKey: true }))

    expect(onSend).not.toHaveBeenCalled()
  })
})

// ── File upload tests ────────────────────────────────────────────

describe('InputBar - file upload on send', () => {
  let mockFetch: ReturnType<typeof vi.fn>

  beforeEach(() => {
    mockFetch = vi.fn()
    globalThis.fetch = mockFetch as typeof fetch
  })

  function makeFile(name: string, size = 100): File {
    return new File(['x'.repeat(size)], name, { type: 'application/octet-stream' })
  }

  function getSendButton() {
    return document.querySelector('.btn-send') as HTMLButtonElement
  }

  it('selecting files stores them as pending, does NOT upload yet', async () => {
    renderInputBar()
    const file = makeFile('test.pdf')

    const fileInput = document.querySelector('input[type="file"]')!
    fireEvent.change(fileInput, { target: { files: [file] } })

    // File should appear in the UI
    expect(screen.getByText('test.pdf')).toBeInTheDocument()
    // No fetch should have been called
    expect(mockFetch).not.toHaveBeenCalled()

    // Sending should trigger upload first
    const textarea = screen.getByPlaceholderText(/Enter instruction/)
    fireEvent.change(textarea, { target: { value: 'process this' } })
    mockFetch.mockResolvedValueOnce({ ok: true })
    fireEvent.submit(document.querySelector('form')!)

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalled()
    })
  })

  it('send button is disabled while files are uploading', async () => {
    renderInputBar()
    const file = makeFile('big.xlsx')

    // Select file — stored as pending
    const fileInput = document.querySelector('input[type="file"]')!
    fireEvent.change(fileInput, { target: { files: [file] } })

    // Add text
    const textarea = screen.getByPlaceholderText(/Enter instruction/)
    fireEvent.change(textarea, { target: { value: 'analyze' } })

    // Mock a never-resolving upload
    const uploadPromise = new Promise<{ ok: boolean }>(() => {})
    mockFetch.mockReturnValue(uploadPromise)

    fireEvent.submit(document.querySelector('form')!)

    // While uploading, "Uploading files..." status text should appear
    await waitFor(() => {
      expect(screen.getByText('Uploading files...')).toBeInTheDocument()
    })

    // Send button should be disabled
    expect(getSendButton()).toBeDisabled()
  })

  it('failed upload shows retry and remove buttons, blocks send', async () => {
    const { onSend } = renderInputBar()
    const file = makeFile('data.csv')

    const fileInput = document.querySelector('input[type="file"]')!
    fireEvent.change(fileInput, { target: { files: [file] } })

    const textarea = screen.getByPlaceholderText(/Enter instruction/)
    fireEvent.change(textarea, { target: { value: 'process' } })

    // Mock failed upload
    mockFetch.mockResolvedValueOnce({ ok: false, json: () => Promise.resolve({}) })

    fireEvent.submit(document.querySelector('form')!)

    await waitFor(() => {
      // Failed state: should show retry + remove
      expect(screen.getByText('data.csv')).toBeInTheDocument()
      expect(screen.getByLabelText(/retry.*data\.csv/i)).toBeInTheDocument()
      expect(screen.getByLabelText(/remove.*data\.csv/i)).toBeInTheDocument()
    })

    // Should NOT have called onSend because upload failed
    expect(onSend).not.toHaveBeenCalled()
  })

  it('retry button re-triggers upload for failed file', async () => {
    const { onSend } = renderInputBar()
    const file = makeFile('report.xlsx')

    const fileInput = document.querySelector('input[type="file"]')!
    fireEvent.change(fileInput, { target: { files: [file] } })

    const textarea = screen.getByPlaceholderText(/Enter instruction/)
    fireEvent.change(textarea, { target: { value: 'summarize' } })

    // First attempt fails
    mockFetch.mockResolvedValueOnce({ ok: false })
    fireEvent.submit(document.querySelector('form')!)

    await waitFor(() => {
      expect(screen.getByLabelText(/retry.*report\.xlsx/i)).toBeInTheDocument()
    })

    // Set up successful mock BEFORE clicking retry
    mockFetch.mockResolvedValueOnce({ ok: true })

    // Click retry — this triggers upload which will hit the success mock
    fireEvent.click(screen.getByLabelText(/retry.*report\.xlsx/i))

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2)
    })

    // Wait for state to update after retry completes
    await waitFor(() => {
      expect(screen.queryByLabelText(/retry.*report\.xlsx/i)).not.toBeInTheDocument()
    })

    // Now send should work
    fireEvent.submit(document.querySelector('form')!)

    await waitFor(() => {
      expect(onSend).toHaveBeenCalledWith('@report.xlsx summarize', [file])
    })
  })

  it('remove button removes failed file from list, allows send', async () => {
    const { onSend } = renderInputBar()
    const file = makeFile('bad.pdf')

    const fileInput = document.querySelector('input[type="file"]')!
    fireEvent.change(fileInput, { target: { files: [file] } })

    const textarea = screen.getByPlaceholderText(/Enter instruction/)
    fireEvent.change(textarea, { target: { value: 'hello' } })

    // First attempt fails
    mockFetch.mockResolvedValueOnce({ ok: false })
    fireEvent.submit(document.querySelector('form')!)

    await waitFor(() => {
      expect(screen.getByLabelText(/remove.*bad\.pdf/i)).toBeInTheDocument()
    })

    // Click remove
    fireEvent.click(screen.getByLabelText(/remove.*bad\.pdf/i))

    // File should be gone
    expect(screen.queryByText('bad.pdf')).not.toBeInTheDocument()

    // Now send works with just the text
    fireEvent.submit(document.querySelector('form')!)

    await waitFor(() => {
      expect(onSend).toHaveBeenCalledWith('hello', undefined)
    })
  })
})

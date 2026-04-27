import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import MessageBubble, { formatBashCommand, formatFileContent, parseTagBlocks, hasIncompleteTag } from '../components/MessageBubble'
import type { Message } from '../lib/types'

function renderMessage(message: Message, overrides?: Partial<React.ComponentProps<typeof MessageBubble>>) {
  return render(
    <MessageBubble
      message={message}
      sessionId="test-session"
      onAnswer={() => {}}
      {...overrides}
    />
  )
}

describe('MessageBubble - file upload display', () => {
  it('renders file cards in a user message when files are attached', () => {
    const message: Message = {
      type: 'user',
      content: 'Review this file',
      index: 0,
      data: [
        { filename: 'report.pdf', size: 102400 },
        { filename: 'data.csv', size: 5120 },
      ],
    }

    renderMessage(message)

    // Both filenames should be visible in file cards
    expect(screen.getByText('report.pdf')).toBeInTheDocument()
    expect(screen.getByText('data.csv')).toBeInTheDocument()
  })

  it('renders user message text AND files together', () => {
    const message: Message = {
      type: 'user',
      content: 'Please analyze this document',
      index: 0,
      data: [
        { filename: 'notes.txt', size: 256 },
      ],
    }

    renderMessage(message)

    // Text content should still be visible
    expect(screen.getByText('Please analyze this document')).toBeInTheDocument()
    // File should also be visible
    expect(screen.getByText('notes.txt')).toBeInTheDocument()
  })

  it('renders user message without files when no data is present', () => {
    const message: Message = {
      type: 'user',
      content: 'Just a text message',
      index: 0,
    }

    renderMessage(message)

    expect(screen.getByText('Just a text message')).toBeInTheDocument()
  })

  it('renders user message with empty files array as text only', () => {
    const message: Message = {
      type: 'user',
      content: 'No files here',
      index: 0,
      data: [],
    }

    renderMessage(message)

    expect(screen.getByText('No files here')).toBeInTheDocument()
  })

  it('renders file-only message bubble when content is empty but files exist', () => {
    const message: Message = {
      type: 'user',
      content: '',
      index: 0,
      data: [
        { filename: 'standalone.pdf', size: 204800 },
      ],
    }

    renderMessage(message)

    // File should be visible even without text content
    expect(screen.getByText('standalone.pdf')).toBeInTheDocument()
  })

  it('renders file-only message bubble with multiple files', () => {
    const message: Message = {
      type: 'user',
      content: '',
      index: 0,
      data: [
        { filename: 'image1.png', size: 102400 },
        { filename: 'image2.png', size: 204800 },
      ],
    }

    renderMessage(message)

    expect(screen.getByText('image1.png')).toBeInTheDocument()
    expect(screen.getByText('image2.png')).toBeInTheDocument()
  })
})

describe('MessageBubble - file click to reference', () => {
  it('calls onFileClick when user clicks a file card in a user message', () => {
    const onFileClick = vi.fn()
    const message: Message = {
      type: 'user',
      content: 'Review this',
      index: 0,
      data: [
        { filename: 'report.pdf', size: 102400 },
      ],
    }

    render(
      <MessageBubble
        message={message}
        sessionId="test-session"
        onAnswer={() => {}}
        onFileClick={onFileClick}
      />
    )

    screen.getByText('report.pdf').click()
    expect(onFileClick).toHaveBeenCalledWith('report.pdf')
  })

  it('does not call onFileClick when onFileClick is not provided', () => {
    const message: Message = {
      type: 'user',
      content: 'Review this',
      index: 0,
      data: [
        { filename: 'report.pdf', size: 102400 },
      ],
    }

    // Should not crash when onFileClick is not provided
    renderMessage(message)
    expect(screen.getByText('report.pdf')).toBeInTheDocument()
  })

  it('calls onFileClick for file-only messages', () => {
    const onFileClick = vi.fn()
    const message: Message = {
      type: 'user',
      content: '',
      index: 0,
      data: [
        { filename: 'data.csv', size: 5120 },
      ],
    }

    render(
      <MessageBubble
        message={message}
        sessionId="test-session"
        onAnswer={() => {}}
        onFileClick={onFileClick}
      />
    )

    screen.getByText('data.csv').click()
    expect(onFileClick).toHaveBeenCalledWith('data.csv')
  })
})

describe('MessageBubble - stream event display', () => {
  it('renders stream_event with tool name when event has tool_use type', () => {
    const message: Message = {
      type: 'stream_event',
      content: '',
      index: 1,
      uuid: 'evt-123',
      event: {
        type: 'tool_use',
        tool_name: 'Read',
      },
    }

    renderMessage(message)

    expect(screen.getByText(/Read/)).toBeInTheDocument()
  })

  it('renders stream_event as compact activity indicator', () => {
    const message: Message = {
      type: 'stream_event',
      content: '',
      index: 2,
      uuid: 'evt-456',
      event: {
        type: 'tool_use',
        tool_name: 'Bash',
      },
    }

    renderMessage(message)

    // Should be visible and look like an activity indicator, not a full message
    const el = screen.getByText(/Bash/)
    expect(el).toBeInTheDocument()
    expect(el.closest('.stream-event')).toBeInTheDocument()
  })

  it('renders stream_event with progress info when available', () => {
    const message: Message = {
      type: 'stream_event',
      content: '',
      index: 3,
      uuid: 'evt-789',
      event: {
        type: 'progress',
        message: 'Processing 3 of 10 files...',
      },
    }

    renderMessage(message)

    expect(screen.getByText(/Processing 3 of 10 files/)).toBeInTheDocument()
  })

  it('hides stream_event when event type is unknown or missing', () => {
    const message: Message = {
      type: 'stream_event',
      content: '',
      index: 4,
      uuid: 'evt-unknown',
      event: {},
    }

    const { container } = renderMessage(message)

    // Should render nothing for unknown events
    expect(container.firstChild).toBeNull()
  })
})

describe('MessageBubble - file_result (agent-generated files)', () => {
  it('renders file cards for file_result messages with download links', () => {
    const message: Message = {
      type: 'file_result',
      content: '',
      index: 10,
      session_id: 'session-1',
      data: [
        { filename: 'report.pdf', size: 51200 },
      ],
    }

    renderMessage(message)

    expect(screen.getByText('report.pdf')).toBeInTheDocument()
  })

  it('renders file cards with downloadUrl when user_id and session_id are available', () => {
    const message: Message = {
      type: 'file_result',
      content: '',
      index: 10,
      session_id: 'session-1',
      user_id: 'user-123',
      data: [
        { filename: 'export.csv', size: 2048 },
      ],
    }

    renderMessage(message)

    const link = screen.getByRole('link', { name: /download export\.csv/i })
    expect(link).toHaveAttribute('href', '/api/users/user-123/download/outputs/export.csv')
  })

  it('renders multiple generated files', () => {
    const message: Message = {
      type: 'file_result',
      content: '',
      index: 10,
      session_id: 'session-1',
      data: [
        { filename: 'chart1.png', size: 102400 },
        { filename: 'chart2.png', size: 204800 },
        { filename: 'summary.pdf', size: 51200 },
      ],
    }

    renderMessage(message)

    expect(screen.getByText('chart1.png')).toBeInTheDocument()
    expect(screen.getByText('chart2.png')).toBeInTheDocument()
    expect(screen.getByText('summary.pdf')).toBeInTheDocument()
  })
})

describe('MessageBubble - user message Markdown rendering', () => {
  it('renders headings in user message content', () => {
    const message: Message = {
      type: 'user',
      content: '## Analyze this\n\nSome text',
      index: 0,
    }

    renderMessage(message)

    const heading = screen.getByRole('heading', { level: 2 })
    expect(heading).toHaveTextContent('Analyze this')
  })

  it('renders lists in user message content', () => {
    const message: Message = {
      type: 'user',
      content: '- item one\n- item two\n- item three',
      index: 0,
    }

    renderMessage(message)

    expect(screen.getByText('item one')).toBeInTheDocument()
    expect(screen.getByText('item two')).toBeInTheDocument()
    expect(screen.getByText('item three')).toBeInTheDocument()
  })

  it('renders code blocks in user message content', () => {
    const message: Message = {
      type: 'user',
      content: 'Check this code:\n\n```typescript\nconst x = 1\n```\nDone',
      index: 0,
    }

    renderMessage(message)

    // Code block should render with syntax highlighting
    // "const" is in its own hljs-keyword span
    expect(screen.getByText('const')).toHaveClass('hljs-keyword')
    // Code element should have hljs class
    const codeEl = screen.getByText('const').closest('code')
    expect(codeEl).toHaveClass('hljs')
  })

  it('renders links in user message content', () => {
    const message: Message = {
      type: 'user',
      content: 'See [this link](https://example.com)',
      index: 0,
    }

    renderMessage(message)

    const link = screen.getByRole('link', { name: 'this link' })
    expect(link).toHaveAttribute('href', 'https://example.com')
  })
})

describe('MessageBubble - code block syntax highlighting', () => {
  it('applies syntax highlighting classes to code blocks in assistant messages', () => {
    const message: Message = {
      type: 'assistant',
      content: '```typescript\nconst x: number = 1\n```',
      index: 0,
    }

    renderMessage(message)

    // Code element should have hljs classes from highlight.js
    const codeEl = screen.getByText(/number/)
    expect(codeEl.closest('code')).toHaveClass('hljs')
  })

  it('renders copy button on code blocks', () => {
    const message: Message = {
      type: 'assistant',
      content: '```python\nprint("hello")\n```',
      index: 0,
    }

    renderMessage(message)

    // Should have a copy button
    const copyBtn = screen.getByRole('button', { name: /copy/i })
    expect(copyBtn).toBeInTheDocument()
  })

  it('shows language label on code blocks', () => {
    const message: Message = {
      type: 'assistant',
      content: '```javascript\nconsole.log("hi")\n```',
      index: 0,
    }

    renderMessage(message)

    // Should show the language name in the code block header
    const langBadge = screen.getByText('javascript')
    expect(langBadge).toHaveClass('code-block-lang')
  })
})

describe('MessageBubble - user bubble styling', () => {
  it('renders user bubble with correct class for white background styling', () => {
    const message: Message = {
      type: 'user',
      content: 'Hello world',
      index: 0,
    }

    const { container } = renderMessage(message)
    const bubble = container.querySelector('.user-message .bubble')

    // Bubble element should exist and have the expected class
    expect(bubble).toBeInTheDocument()
  })
})

describe('MessageBubble - assistant bubble styling', () => {
  it('renders assistant bubble without border', () => {
    const message: Message = {
      type: 'assistant',
      content: 'Hello world',
      index: 0,
    }

    const { container } = renderMessage(message)
    const bubble = container.querySelector('.assistant-message .bubble')!

    const style = getComputedStyle(bubble)
    // Log to debug: JSDom may not fully support CSS computed styles
    // Check both border width and the border style
    const borderWidth = style.borderTopWidth
    const borderStyle = style.borderTopStyle

    // No border means 0 width OR no style (none/hidden)
    expect(borderStyle === 'none' || borderStyle === '' || borderWidth === '0px').toBe(true)
  })
})

describe('MessageBubble - result message (Session completed)', () => {
  it('hides the result message bubble — no "Session completed" shown', () => {
    const message: Message = {
      type: 'result',
      content: '',
      index: 10,
      session_id: 'session-1',
      duration_ms: 64500,
      total_cost_usd: 0.3835,
      subtype: 'complete',
    }

    const { container } = renderMessage(message)
    expect(container.firstChild).toBeNull()
  })

  it('still hides result message without duration or cost', () => {
    const message: Message = {
      type: 'result',
      content: '',
      index: 10,
      session_id: 'session-1',
    }

    const { container } = renderMessage(message)
    expect(container.firstChild).toBeNull()
  })
})

describe('MessageBubble - tool result rendering', () => {
  it('renders tool_result content as Markdown, not plain pre', () => {
    const message: Message = {
      type: 'tool_result',
      content: '**bold text** and `code`',
      index: 5,
      name: 'Read',
    }

    renderMessage(message)

    // Bold should be rendered as <strong>, not plain text
    const strong = screen.getByRole('strong')
    expect(strong).toHaveTextContent('bold text')
  })

  it('renders JSON tool result as formatted code block', () => {
    const message: Message = {
      type: 'tool_result',
      content: '{"key": "value", "count": 42}',
      index: 5,
      name: 'Bash',
    }

    renderMessage(message)

    // JSON should be rendered, not just raw pre text
    expect(screen.getByText(/"key"/)).toBeInTheDocument()
  })

  it('hides tool_result when content is empty', () => {
    const message: Message = {
      type: 'tool_result',
      content: '',
      index: 5,
      name: 'TaskOutput',
    }

    const { container } = renderMessage(message)
    expect(container.firstChild).toBeNull()
  })

  it('hides tool_result when content field is missing', () => {
    const message: Message = {
      type: 'tool_result',
      index: 5,
      name: 'TaskOutput',
    } as Message

    const { container } = renderMessage(message)
    expect(container.firstChild).toBeNull()
  })

  it('shows tool_result when is_error is true', () => {
    const message: Message = {
      type: 'tool_result',
      content: 'fatal: pathspec did not match any files',
      index: 5,
      name: 'Bash',
      is_error: true,
    }

    const { container } = renderMessage(message)
    const details = container.querySelector('details.tool-result')
    expect(details).toBeInTheDocument()
    expect(details?.querySelector('summary')).toHaveTextContent('Result: Bash')
    expect(screen.getByText('fatal: pathspec did not match any files')).toBeInTheDocument()
  })

  it('shows error type message as bubble', () => {
    const message: Message = {
      type: 'error',
      content: '',
      message: 'Connection refused: timeout',
      index: 10,
    }

    const { container } = renderMessage(message)
    expect(container.querySelector('.error-message')).toBeInTheDocument()
    expect(container.querySelector('.bubble.error')).toBeInTheDocument()
    expect(screen.getByText('Connection refused: timeout')).toBeInTheDocument()
  })

  it('falls back to content field for error messages when message field is empty', () => {
    const message: Message = {
      type: 'error',
      content: 'Task execution failed',
      index: 11,
    }

    const { container } = renderMessage(message)
    expect(container.querySelector('.error-message')).toBeInTheDocument()
    expect(screen.getByText('Task execution failed')).toBeInTheDocument()
  })
})

describe('MessageBubble - TodoWrite visualization', () => {
  it('renders TodoWrite tool_use as visual todo list', () => {
    const message: Message = {
      type: 'tool_use',
      content: '',
      index: 5,
      name: 'TodoWrite',
      input: {
        todos: [
          { content: 'Check files', status: 'completed' },
          { content: 'Extract PDF', status: 'in_progress', activeForm: '正在提取' },
          { content: 'Fill Excel', status: 'pending' },
        ],
      },
    }

    renderMessage(message)

    // Should NOT show the raw JSON collapse
    expect(screen.queryByText('{"todos"')).not.toBeInTheDocument()

    // Should show visual todo items
    expect(screen.getByText('Check files')).toBeInTheDocument()
    expect(screen.getByText('Extract PDF')).toBeInTheDocument()
    expect(screen.getByText('Fill Excel')).toBeInTheDocument()
  })

  it('falls back to JSON collapse for TodoWrite with empty todos', () => {
    const message: Message = {
      type: 'tool_use',
      content: '',
      index: 5,
      name: 'TodoWrite',
      input: { todos: [] },
    }

    const { container } = renderMessage(message)

    // Should fall back to the <details> JSON display
    const details = container.querySelector('details.tool-message')
    expect(details).toBeInTheDocument()
  })

  it('renders TodoWrite progress indicator', () => {
    const message: Message = {
      type: 'tool_use',
      content: '',
      index: 5,
      name: 'TodoWrite',
      input: {
        todos: [
          { content: 'A', status: 'completed' },
          { content: 'B', status: 'completed' },
          { content: 'C', status: 'pending' },
        ],
      },
    }

    renderMessage(message)

    // Progress bar should exist
    const progressBar = screen.getByRole('progressbar')
    expect(progressBar).toHaveAttribute('aria-valuenow', '67')
  })

  it('hides older TodoWrite when lastTodoWriteIndex is provided', () => {
    const oldTodo: Message = {
      type: 'tool_use',
      content: '',
      index: 2,
      name: 'TodoWrite',
      input: {
        todos: [
          { content: 'Old task', status: 'pending' },
        ],
      },
    }

    renderMessage(oldTodo, { lastTodoWriteIndex: 10 })

    // Older todo should NOT be visible
    expect(screen.queryByText('Old task')).not.toBeInTheDocument()
  })

  it('shows latest TodoWrite when index matches lastTodoWriteIndex', () => {
    const latestTodo: Message = {
      type: 'tool_use',
      content: '',
      index: 10,
      name: 'TodoWrite',
      input: {
        todos: [
          { content: 'New task', status: 'in_progress' },
        ],
      },
    }

    renderMessage(latestTodo, { lastTodoWriteIndex: 10 })

    // Latest todo should be visible
    expect(screen.getByText('New task')).toBeInTheDocument()
  })
})

describe('MessageBubble - multiple files as separate bubbles', () => {
  it('renders each uploaded user file as a separate right-aligned bubble', () => {
    const message: Message = {
      type: 'user',
      content: 'Here are my files',
      index: 0,
      data: [
        { filename: 'a.pdf', size: 1000 },
        { filename: 'b.docx', size: 2000 },
      ],
    }

    const { container } = renderMessage(message)
    const fileBubbles = container.querySelectorAll('.user-file-message')
    expect(fileBubbles.length).toBe(2)
    expect(fileBubbles[0].querySelector('.file-card-name')?.textContent).toBe('a.pdf')
    expect(fileBubbles[1].querySelector('.file-card-name')?.textContent).toBe('b.docx')
  })

  it('renders user file bubble separately from text bubble', () => {
    const message: Message = {
      type: 'user',
      content: 'Review this',
      index: 0,
      data: [
        { filename: 'report.pdf', size: 1000 },
      ],
    }

    const { container } = renderMessage(message)
    // Text in a user-message bubble, file in a user-file-message bubble
    expect(container.querySelector('.user-message .bubble')).toBeInTheDocument()
    expect(container.querySelector('.user-file-message')).toBeInTheDocument()
  })

  it('renders file bubbles before text bubble when user message has both', () => {
    const message: Message = {
      type: 'user',
      content: '总结一下',
      index: 0,
      data: [
        { filename: 'a.pdf', size: 1000 },
        { filename: 'b.pdf', size: 2000 },
      ],
    }

    const { container } = renderMessage(message)
    // When a user message contains both text and files, files should appear first
    // (files uploaded first, then the text message below)
    const allMessages = container.querySelectorAll('.message')
    // First message should be the file, last should be the text
    expect(allMessages[0]).toHaveClass('user-file-message')
    expect(allMessages[allMessages.length - 1]).toHaveClass('user-message')
  })

  it('renders each agent-generated file as a separate left-aligned bubble', () => {
    const message: Message = {
      type: 'file_result',
      content: '',
      index: 10,
      session_id: 'session-1',
      user_id: 'user-1',
      data: [
        { filename: 'report.docx', size: 5000, download_url: '/api/users/user-1/download/outputs/report.docx' },
        { filename: 'data.xlsx', size: 3000, download_url: '/api/users/user-1/download/outputs/data.xlsx' },
      ],
    }

    const { container } = renderMessage(message)
    const fileMessages = container.querySelectorAll('.generated-file-message')
    // Each file should have its own bubble with the generated-file-message class
    expect(fileMessages.length).toBe(2)
    // Should be left-aligned (not centered like system-message)
    expect(fileMessages[0]).toHaveClass('generated-file-message')
    expect(fileMessages[0].querySelector('.file-card-name')?.textContent).toBe('report.docx')
    expect(fileMessages[1].querySelector('.file-card-name')?.textContent).toBe('data.xlsx')
  })

  it('renders single file as one bubble', () => {
    const message: Message = {
      type: 'file_result',
      content: '',
      index: 10,
      session_id: 'session-1',
      user_id: 'user-1',
      data: [
        { filename: 'single.pdf', size: 1000, download_url: '/api/users/user-1/download/outputs/single.pdf' },
      ],
    }

    const { container } = renderMessage(message)
    const fileMessages = container.querySelectorAll('.generated-file-message')
    expect(fileMessages.length).toBe(1)
  })
})

describe('MessageBubble - formatBashCommand helper', () => {
  it('unescapes \\n to real newlines in command', () => {
    const result = formatBashCommand({
      command: 'python3 -c "\\nimport openpyxl\\nwb = openpyxl.load_workbook()\\n"',
      description: 'analyze excel file',
    })
    expect(result.command).toBe('python3 -c "\nimport openpyxl\nwb = openpyxl.load_workbook()\n"')
  })

  it('unescapes \\r\\n to real newlines in command', () => {
    const result = formatBashCommand({
      command: 'echo line1\\r\\necho line2',
    })
    expect(result.command).toBe('echo line1\r\necho line2')
  })

  it('extracts description from input', () => {
    const result = formatBashCommand({
      command: 'ls -la',
      description: 'list files',
    })
    expect(result.description).toBe('list files')
  })

  it('returns null description when not present', () => {
    const result = formatBashCommand({
      command: 'ls -la',
    })
    expect(result.description).toBeNull()
  })

  it('handles missing command field gracefully', () => {
    const result = formatBashCommand({})
    expect(result.command).toBe('')
    expect(result.description).toBeNull()
  })

  it('handles non-string description', () => {
    const result = formatBashCommand({
      command: 'echo test',
      description: 123,
    })
    expect(result.description).toBe('123')
  })

  it('coerces non-string command to string', () => {
    const result = formatBashCommand({
      command: 42,
    })
    expect(result.command).toBe('42')
  })

  it('handles empty command string', () => {
    const result = formatBashCommand({
      command: '',
      description: '',
    })
    expect(result.command).toBe('')
    expect(result.description).toBe('')
  })

  it('preserves \\t and other escape sequences', () => {
    const result = formatBashCommand({
      command: 'echo "hello\\tworld"',
    })
    // \\t should NOT be converted (only \\n and \\r\\n)
    expect(result.command).toBe('echo "hello\\tworld"')
  })
})

describe('MessageBubble - Bash tool_use rendering', () => {
  it('renders Bash tool_use with description above formatted command', () => {
    const message: Message = {
      type: 'tool_use',
      content: '',
      index: 5,
      name: 'Bash',
      input: {
        command: 'python3 -c "\\nimport openpyxl\\nprint(hello)\\n"',
        description: 'analyze excel file',
      },
    }

    const { container } = renderMessage(message)

    // Description should appear above the code block
    expect(screen.getByText('analyze excel file')).toBeInTheDocument()

    // Command should have real newlines, not \\n literals
    const codeBlock = container.querySelector('.tool-input code')
    expect(codeBlock?.textContent).toContain('\n')
    expect(codeBlock?.textContent).not.toContain('\\n')
  })

  it('renders Bash tool_use without description when not provided', () => {
    const message: Message = {
      type: 'tool_use',
      content: '',
      index: 5,
      name: 'Bash',
      input: {
        command: 'ls -la',
      },
    }

    const { container } = renderMessage(message)

    // Should still show the collapsible details
    const details = container.querySelector('details.tool-message')
    expect(details).toBeInTheDocument()

    // No description element should be present
    expect(container.querySelector('.tool-description')).not.toBeInTheDocument()
  })

  it('renders non-Bash tool_use as raw JSON (unchanged)', () => {
    const message: Message = {
      type: 'tool_use',
      content: '',
      index: 5,
      name: 'Read',
      input: { path: '/some/file.txt' },
    }

    renderMessage(message)

    // Should contain raw JSON
    expect(screen.getByText(/"path"/)).toBeInTheDocument()
    expect(screen.getByText(/"\/some\/file\.txt"/)).toBeInTheDocument()
  })

  it('keeps collapsible details for Bash tool_use', () => {
    const message: Message = {
      type: 'tool_use',
      content: '',
      index: 5,
      name: 'Bash',
      input: {
        command: 'echo test',
        description: 'run test',
      },
    }

    const { container } = renderMessage(message)

    const details = container.querySelector('details.tool-message')
    expect(details).toBeInTheDocument()
    expect(details).not.toHaveAttribute('open')
  })

  it('shows tool name and icon in summary for Bash', () => {
    const message: Message = {
      type: 'tool_use',
      content: '',
      index: 5,
      name: 'Bash',
      input: { command: 'echo hi' },
    }

    const { container } = renderMessage(message)

    expect(screen.getByText('Bash')).toBeInTheDocument()
    const summary = container.querySelector('.tool-summary')
    expect(summary).toBeInTheDocument()
  })
})

describe('MessageBubble - formatFileContent helper', () => {
  it('unescapes \\n to real newlines in content', () => {
    const result = formatFileContent({
      file_path: '/path/to/file.py',
      content: '#!/usr/bin/env python3\\nimport os\\nprint("hello")\\n',
    })
    expect(result.content).toBe('#!/usr/bin/env python3\nimport os\nprint("hello")\n')
  })

  it('extracts file_path from input', () => {
    const result = formatFileContent({
      file_path: '/some/path.py',
      content: 'print(1)',
    })
    expect(result.filePath).toBe('/some/path.py')
  })

  it('returns null file_path when not present', () => {
    const result = formatFileContent({})
    expect(result.filePath).toBeNull()
  })

  it('handles missing content field gracefully', () => {
    const result = formatFileContent({})
    expect(result.content).toBe('')
  })

  it('handles empty content', () => {
    const result = formatFileContent({
      file_path: '/f.py',
      content: '',
    })
    expect(result.content).toBe('')
  })

  it('unescapes \\r\\n to real newlines', () => {
    const result = formatFileContent({
      content: 'line1\\r\\nline2',
    })
    expect(result.content).toBe('line1\r\nline2')
  })
})

describe('MessageBubble - hidden system message subtypes', () => {
  it('hides system messages with subtype task_started', () => {
    const message: Message = {
      type: 'system',
      subtype: 'task_started',
      content: '',
      index: 0,
    }

    const { container } = renderMessage(message)
    expect(container.firstChild).toBeNull()
  })

  it('hides system messages with subtype task_started.todos', () => {
    const message: Message = {
      type: 'system',
      subtype: 'task_started.todos',
      content: '',
      index: 0,
    }

    const { container } = renderMessage(message)
    expect(container.firstChild).toBeNull()
  })

  it('hides system messages with subtype starting with task_started.', () => {
    const message: Message = {
      type: 'system',
      subtype: 'task_started.some_detail',
      content: '',
      index: 0,
    }

    const { container } = renderMessage(message)
    expect(container.firstChild).toBeNull()
  })

  it('hides system messages with subtype session_cancelled', () => {
    const message: Message = {
      type: 'system',
      subtype: 'session_cancelled',
      content: 'Session cancelled by user.',
      index: 0,
    }

    const { container } = renderMessage(message)
    expect(container.firstChild).toBeNull()
  })

  it('still shows non-task_started system messages', () => {
    const message: Message = {
      type: 'system',
      subtype: 'some_other',
      content: 'Visible message',
      index: 0,
    }

    renderMessage(message)
    expect(screen.getByText('Visible message')).toBeInTheDocument()
  })
})

describe('MessageBubble - Bash tool_use with empty command', () => {
  it('returns null when Bash command is empty string', () => {
    const message: Message = {
      type: 'tool_use',
      content: '',
      index: 5,
      name: 'Bash',
      input: { command: '', description: '' },
    }

    const { container } = renderMessage(message)
    expect(container.firstChild).toBeNull()
  })

  it('returns null when Bash command field is missing', () => {
    const message: Message = {
      type: 'tool_use',
      content: '',
      index: 5,
      name: 'Bash',
      input: { description: 'some description' },
    }

    const { container } = renderMessage(message)
    expect(container.firstChild).toBeNull()
  })

  it('returns null when Bash input is undefined', () => {
    const message: Message = {
      type: 'tool_use',
      content: '',
      index: 5,
      name: 'Bash',
    }

    const { container } = renderMessage(message)
    expect(container.firstChild).toBeNull()
  })

  it('renders Bash tool_use when command is non-empty', () => {
    const message: Message = {
      type: 'tool_use',
      content: '',
      index: 5,
      name: 'Bash',
      input: { command: 'echo hello' },
    }

    const { container } = renderMessage(message)
    expect(container.querySelector('details.tool-message')).toBeInTheDocument()
  })
})

describe('MessageBubble - Write tool_use rendering', () => {
  it('renders Write tool_use with file path and formatted content', () => {
    const message: Message = {
      type: 'tool_use',
      content: '',
      index: 5,
      name: 'Write',
      input: {
        file_path: '/path/to/script.py',
        content: '#!/usr/bin/env python3\\nimport os\\nprint("hello")\\n',
      },
    }

    const { container } = renderMessage(message)

    // File path should appear in the summary detail area
    const summary = container.querySelector('.tool-detail')
    expect(summary?.textContent).toContain('script.py')

    // Content should have real newlines, not \\n literals
    const codeBlock = container.querySelector('.tool-input code')
    expect(codeBlock?.textContent).toContain('\n')
    expect(codeBlock?.textContent).not.toContain('\\n')
  })

  it('renders Write tool_use with file path as description', () => {
    const message: Message = {
      type: 'tool_use',
      content: '',
      index: 5,
      name: 'Write',
      input: {
        file_path: '/f.py',
        content: 'x = 1',
      },
    }

    const { container } = renderMessage(message)

    expect(container.querySelector('details.tool-message')).toBeInTheDocument()
    // File path appears as description above the code block
    const desc = container.querySelector('.tool-description')
    expect(desc?.textContent).toBe('/f.py')
  })

  it('keeps collapsible details for Write tool_use', () => {
    const message: Message = {
      type: 'tool_use',
      content: '',
      index: 5,
      name: 'Write',
      input: {
        file_path: '/f.py',
        content: 'print(1)',
      },
    }

    const { container } = renderMessage(message)

    const details = container.querySelector('details.tool-message')
    expect(details).toBeInTheDocument()
    expect(details).not.toHaveAttribute('open')
  })
})

describe('MessageBubble - streaming text (content_block_delta)', () => {
  // content_block_delta events are now hidden in MessageBubble
  // Aggregation and display handled by App.tsx useStreamingText hook
  // This prevents duplicate display of streaming text

  it('hides content_block_delta stream event (aggregation in App.tsx)', () => {
    const message: Message = {
      type: 'stream_event',
      content: '',
      index: 1,
      uuid: 'evt-123',
      event: {
        type: 'content_block_delta',
        index: 0,
        delta: {
          type: 'text_delta',
          text: 'Hello',
        },
      },
    }

    const { container } = renderMessage(message)

    // Should return null - not render individual deltas
    expect(container.firstChild).toBeNull()
  })

  it('hides combined content_block_delta event', () => {
    const combinedMessage: Message = {
      type: 'stream_event',
      content: '',
      index: 1,
      uuid: 'evt-combined',
      event: {
        type: 'content_block_delta',
        index: 0,
        delta: {
          type: 'text_delta',
          text: 'Hello world',
        },
      },
    }

    const { container } = renderMessage(combinedMessage)

    // Should return null - aggregation happens in App.tsx
    expect(container.firstChild).toBeNull()
  })

  it('ignores non-text_delta events', () => {
    const message: Message = {
      type: 'stream_event',
      content: '',
      index: 3,
      uuid: 'evt-3',
      event: {
        type: 'content_block_delta',
        index: 0,
        delta: {
          type: 'input_json_delta', // Not text_delta
          partial_json: '{"key": "val',
        },
      },
    }

    const { container } = renderMessage(message)

    // Non-text deltas should not render
    expect(container.firstChild).toBeNull()
  })
})

describe('parseTagBlocks', () => {
  it('returns single text block when no tags present', () => {
    const result = parseTagBlocks('Hello world')
    expect(result).toEqual([{ kind: 'text', content: 'Hello world' }])
  })

  it('parses analysis tag', () => {
    const result = parseTagBlocks('<analysis>deep thinking here</analysis>')
    expect(result).toEqual([
      { kind: 'analysis', content: 'deep thinking here' },
    ])
  })

  it('parses summary tag', () => {
    const result = parseTagBlocks('<summary>key points</summary>')
    expect(result).toEqual([
      { kind: 'summary', content: 'key points' },
    ])
  })

  it('parses mixed tags and text', () => {
    const result = parseTagBlocks('<analysis>think</analysis>some text<summary>sum</summary>')
    expect(result).toHaveLength(3)
    expect(result[0]).toEqual({ kind: 'analysis', content: 'think' })
    expect(result[1]).toEqual({ kind: 'text', content: 'some text' })
    expect(result[2]).toEqual({ kind: 'summary', content: 'sum' })
  })

  it('trims whitespace from tag content', () => {
    const result = parseTagBlocks('<analysis>\n  content with spaces  \n</analysis>')
    expect(result[0]).toEqual({ kind: 'analysis', content: 'content with spaces' })
  })

  it('handles multiple analysis tags', () => {
    const result = parseTagBlocks('<analysis>A</analysis><analysis>B</analysis>')
    expect(result).toHaveLength(2)
    expect(result[0]).toEqual({ kind: 'analysis', content: 'A' })
    expect(result[1]).toEqual({ kind: 'analysis', content: 'B' })
  })

  it('handles empty input', () => {
    const result = parseTagBlocks('')
    expect(result).toEqual([{ kind: 'text', content: '' }])
  })

  it('preserves markdown inside tags', () => {
    const result = parseTagBlocks('<analysis>## Heading\n- list item</analysis>')
    expect(result[0].content).toContain('## Heading')
    expect(result[0].content).toContain('- list item')
  })

  it('handles tag with no content', () => {
    const result = parseTagBlocks('<analysis></analysis>text')
    expect(result).toHaveLength(1)
    expect(result[0]).toEqual({ kind: 'text', content: 'text' })
  })
})

describe('hasIncompleteTag', () => {
  it('returns false when no tags present', () => {
    expect(hasIncompleteTag('plain text')).toBe(false)
  })

  it('returns false when all tags are balanced', () => {
    expect(hasIncompleteTag('<analysis>text</analysis><summary>text</summary>')).toBe(false)
  })

  it('returns true when analysis tag is unclosed', () => {
    expect(hasIncompleteTag('<analysis>partial content')).toBe(true)
  })

  it('returns true when summary tag is unclosed', () => {
    expect(hasIncompleteTag('<summary>partial content')).toBe(true)
  })

  it('returns false for empty string', () => {
    expect(hasIncompleteTag('')).toBe(false)
  })

  it('handles multiple tag instances', () => {
    expect(hasIncompleteTag('<analysis>one</analysis><analysis>two')).toBe(true)
  })
})

describe('MessageBubble - assistant message with analysis tag', () => {
  it('renders analysis block as collapsible details', () => {
    const message: Message = {
      type: 'assistant',
      content: '<analysis>step 1: inspect\nstep 2: plan</analysis>Final answer.',
      index: 0,
    }

    const { container } = renderMessage(message)

    const details = container.querySelector('details.analysis-block')
    expect(details).toBeInTheDocument()
    expect(details).not.toHaveAttribute('open')
    const summary = details?.querySelector('summary')
    expect(summary).toHaveTextContent('Analysis')
  })

  it('renders summary block as collapsible details', () => {
    const message: Message = {
      type: 'assistant',
      content: '<summary>## Key Points\n- Point A\n- Point B</summary>Extra text.',
      index: 0,
    }

    const { container } = renderMessage(message)

    const details = container.querySelector('details.summary-block')
    expect(details).toBeInTheDocument()
    expect(details).not.toHaveAttribute('open')
    expect(screen.getByText('Summary')).toBeInTheDocument()
  })

  it('renders markdown inside summary block', () => {
    const message: Message = {
      type: 'assistant',
      content: '<summary>## Title\n\nContent here</summary>',
      index: 0,
    }

    renderMessage(message)

    const heading = screen.getByRole('heading', { level: 2 })
    expect(heading).toHaveTextContent('Title')
  })

  it('renders both analysis and summary together', () => {
    const message: Message = {
      type: 'assistant',
      content: '<analysis>internal reasoning</analysis><summary>user-facing summary</summary>Response text.',
      index: 0,
    }

    const { container } = renderMessage(message)

    expect(container.querySelector('details.analysis-block')).toBeInTheDocument()
    expect(container.querySelector('.summary-block')).toBeInTheDocument()
    expect(screen.getByText('Response text.')).toBeInTheDocument()
  })

  it('renders thinking and analysis/summary together', () => {
    const message: Message = {
      type: 'assistant',
      content: '[thinking]old format[/thinking]<analysis>new format</analysis><summary>summary</summary>done',
      index: 0,
    }

    const { container } = renderMessage(message)

    expect(container.querySelector('details.thinking-block')).toBeInTheDocument()
    expect(container.querySelector('details.analysis-block')).toBeInTheDocument()
    expect(container.querySelector('.summary-block')).toBeInTheDocument()
  })

  it('handles only plain text without tags', () => {
    const message: Message = {
      type: 'assistant',
      content: 'Just a normal response without any tags.',
      index: 0,
    }

    const { container } = renderMessage(message)

    expect(container.querySelector('.analysis-block')).not.toBeInTheDocument()
    expect(container.querySelector('.summary-block')).not.toBeInTheDocument()
    expect(screen.getByText('Just a normal response without any tags.')).toBeInTheDocument()
  })
})

import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import ToolCard from './ToolCard'

// Mock i18n
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        'message.toolRunning': 'Running...',
        'message.result': 'Result',
        'message.errorOccurred': 'Error',
        'message.resultEmpty': 'Empty',
        'message.showAll': 'Show all',
        'message.collapse': 'Collapse',
        'message.copyResult': 'Copy',
        'message.resultCopied': 'Copied!',
      }
      return map[key] || key
    },
    i18n: { language: 'en' },
  }),
}))

// Mock MarkdownRenderer to avoid highlight.js issues in test
vi.mock('./MarkdownRenderer', () => ({
  default: ({ children }: { children: string }) => <pre>{children}</pre>,
}))

describe('ToolCard', () => {
  it('renders tool name and summary', () => {
    render(
      <ToolCard name="Read" summary="3 files">
        <div>content</div>
      </ToolCard>,
    )
    expect(screen.getByText('Read')).toBeTruthy()
    expect(screen.getByText('3 files')).toBeTruthy()
  })

  it('renders running state without tool result', () => {
    render(
      <ToolCard name="Bash" summary="running...">
        <div>cmd</div>
      </ToolCard>,
    )
    expect(screen.getByText('Running...')).toBeTruthy()
  })

  it('renders error state for failed tool', () => {
    render(
      <ToolCard
        name="Bash"
        summary="failed"
        toolResult={{ content: 'error output', is_error: true, name: 'Bash' }}
      >
        <div>cmd</div>
      </ToolCard>,
    )
    expect(screen.getByText('Error')).toBeTruthy()
  })

  it('renders success state for completed tool', () => {
    render(
      <ToolCard
        name="Read"
        summary="done"
        toolResult={{ content: 'file content', name: 'Read' }}
      >
        <div>path</div>
      </ToolCard>,
    )
    expect(screen.getByText('Result')).toBeTruthy()
  })
})

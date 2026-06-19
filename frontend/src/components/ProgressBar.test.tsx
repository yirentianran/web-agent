import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import ProgressBar, { detectPhase, computeToolCounts } from './ProgressBar'
import type { Message } from '../lib/types'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        'progress.label': 'Agent progress',
        'progress.analyze': 'Analyze',
        'progress.edit': 'Edit code',
        'progress.verify': 'Verify',
      }
      return map[key] || key
    },
  }),
}))

function makeToolUse(name: string, index: number): Message {
  return { type: 'tool_use', name, index, content: '', input: {} } as Message
}

describe('ProgressBar', () => {
  it('hides when visible is false', () => {
    const { container } = render(
      <ProgressBar currentPhase="analyze" visible={false} />,
    )
    expect(container.innerHTML).toBe('')
  })

  it('renders all 3 phases', () => {
    render(<ProgressBar currentPhase="analyze" visible={true} />)
    expect(screen.getByText('Analyze')).toBeTruthy()
    expect(screen.getByText('Edit code')).toBeTruthy()
    expect(screen.getByText('Verify')).toBeTruthy()
  })

  it('marks current phase as active', () => {
    render(<ProgressBar currentPhase="edit" visible={true} />)
    const editStep = screen.getByText('Edit code').closest('.progress-step')
    expect(editStep?.className).toContain('progress-step--active')
  })

  it('marks completed phases as done', () => {
    render(<ProgressBar currentPhase="verify" visible={true} />)
    const analyzeStep = screen.getByText('Analyze').closest('.progress-step')
    expect(analyzeStep?.className).toContain('progress-step--done')
  })

  it('has correct ARIA role', () => {
    render(<ProgressBar currentPhase="analyze" visible={true} />)
    expect(screen.getByRole('status')).toBeTruthy()
  })

  it('shows tool count next to phase label', () => {
    render(
      <ProgressBar
        currentPhase="edit"
        visible={true}
        toolCounts={{ analyze: 3, edit: 1, verify: 0 }}
      />,
    )
    expect(screen.getByText('Analyze · 3')).toBeTruthy()
    expect(screen.getByText('Edit code · 1')).toBeTruthy()
    expect(screen.getByText('Verify')).toBeTruthy()
  })
})

describe('detectPhase', () => {
  it('returns analyze for empty messages', () => {
    expect(detectPhase([])).toBe('analyze')
  })

  it('returns analyze when only read/search tools used', () => {
    const msgs = [
      makeToolUse('Read', 1),
      makeToolUse('Grep', 2),
      makeToolUse('Glob', 3),
    ]
    expect(detectPhase(msgs)).toBe('analyze')
  })

  it('returns edit when Write tool is used', () => {
    const msgs = [makeToolUse('Read', 1), makeToolUse('Write', 2)]
    expect(detectPhase(msgs)).toBe('edit')
  })

  it('returns edit when Edit tool is used', () => {
    const msgs = [makeToolUse('Edit', 1)]
    expect(detectPhase(msgs)).toBe('edit')
  })

  it('returns verify when Bash is used', () => {
    const msgs = [
      makeToolUse('Read', 1),
      makeToolUse('Write', 2),
      makeToolUse('Bash', 3),
    ]
    expect(detectPhase(msgs)).toBe('verify')
  })

  it('returns analyze for unknown tools instead of hiding', () => {
    // Agent, Skill, MCP tools, etc. — should NOT cause the bar to disappear
    const msgs = [makeToolUse('Skill', 1), makeToolUse('Agent', 2)]
    expect(detectPhase(msgs)).toBe('analyze')
  })

  it('ignores TodoWrite and AskUserQuestion', () => {
    const msgs = [
      makeToolUse('TodoWrite', 1),
      makeToolUse('AskUserQuestion', 2),
    ]
    expect(detectPhase(msgs)).toBe('analyze')
  })

  it('only considers current turn after last user message', () => {
    const msgs: Message[] = [
      makeToolUse('Read', 1),
      makeToolUse('Write', 2),
      makeToolUse('Bash', 3),
      { type: 'user', index: 4, content: 'new request' } as Message,
      makeToolUse('Read', 5),
      makeToolUse('Grep', 6),
    ]
    expect(detectPhase(msgs)).toBe('analyze')
  })

  it('returns analyze when no tool_use in current turn', () => {
    const msgs: Message[] = [
      makeToolUse('Write', 1),
      makeToolUse('Bash', 2),
      { type: 'user', index: 3, content: 'hello' } as Message,
    ]
    expect(detectPhase(msgs)).toBe('analyze')
  })

  it('detects edit phase in current turn ignoring prior turns', () => {
    const msgs: Message[] = [
      makeToolUse('Read', 1),
      makeToolUse('Write', 2),
      makeToolUse('Bash', 3),
      { type: 'user', index: 4, content: 'fix bug' } as Message,
      makeToolUse('Read', 5),
      makeToolUse('Edit', 6),
    ]
    expect(detectPhase(msgs)).toBe('edit')
  })

  it('verify can appear without prior edit (standalone Bash)', () => {
    const msgs = [makeToolUse('Bash', 1)]
    expect(detectPhase(msgs)).toBe('verify')
  })
})

describe('computeToolCounts', () => {
  it('returns zeros for empty messages', () => {
    expect(computeToolCounts([])).toEqual({ analyze: 0, edit: 0, verify: 0 })
  })

  it('counts unknown tools as analyze', () => {
    const msgs = [
      makeToolUse('Skill', 1),
      makeToolUse('Agent', 2),
    ]
    expect(computeToolCounts(msgs)).toEqual({ analyze: 2, edit: 0, verify: 0 })
  })

  it('counts Write/Edit as edit', () => {
    const msgs = [
      makeToolUse('Read', 1),
      makeToolUse('Write', 2),
      makeToolUse('Edit', 3),
    ]
    expect(computeToolCounts(msgs)).toEqual({ analyze: 1, edit: 2, verify: 0 })
  })

  it('counts Bash as verify', () => {
    const msgs = [
      makeToolUse('Read', 1),
      makeToolUse('Write', 2),
      makeToolUse('Bash', 3),
      makeToolUse('Bash', 4),
    ]
    expect(computeToolCounts(msgs)).toEqual({ analyze: 1, edit: 1, verify: 2 })
  })

  it('skips TodoWrite and AskUserQuestion', () => {
    const msgs = [
      makeToolUse('TodoWrite', 1),
      makeToolUse('AskUserQuestion', 2),
      makeToolUse('Read', 3),
    ]
    expect(computeToolCounts(msgs)).toEqual({ analyze: 1, edit: 0, verify: 0 })
  })

  it('only counts current turn after user message', () => {
    const msgs: Message[] = [
      makeToolUse('Read', 1),
      makeToolUse('Write', 2),
      { type: 'user', index: 3, content: 'next' } as Message,
      makeToolUse('Grep', 4),
      makeToolUse('Grep', 5),
    ]
    expect(computeToolCounts(msgs)).toEqual({ analyze: 2, edit: 0, verify: 0 })
  })
})

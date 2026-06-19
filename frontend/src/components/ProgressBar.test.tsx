import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import ProgressBar, { detectPhase } from './ProgressBar'
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
})

describe('detectPhase', () => {
  it('returns analyze for empty messages', () => {
    expect(detectPhase([])).toBe('analyze')
  })

  it('returns analyze when only Read/Grep/Glob tools used', () => {
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

  it('returns verify when Bash is used after edits', () => {
    const msgs = [
      makeToolUse('Read', 1),
      makeToolUse('Write', 2),
      makeToolUse('Bash', 3),
    ]
    expect(detectPhase(msgs)).toBe('verify')
  })

  it('returns working for mixed unknown tools', () => {
    const msgs = [makeToolUse('Skill', 1), makeToolUse('Agent', 2)]
    expect(detectPhase(msgs)).toBe('working')
  })
})

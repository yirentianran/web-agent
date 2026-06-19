import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ErrorCard from './ErrorCard'

function t(key: string): string {
  const map: Record<string, string> = {
    'message.past': 'Past',
    'message.showDetails': 'Show details',
    'message.hideDetails': 'Hide details',
  }
  return map[key] || key
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t }),
}))

describe('ErrorCard', () => {
  it('renders message and severity icon', () => {
    render(<ErrorCard message="Connection failed" severity="retryable" />)
    expect(screen.getByText('Connection failed')).toBeTruthy()
    expect(screen.getByText('🟡')).toBeTruthy()
  })

  it('renders action buttons and calls onAction on click', () => {
    const onAction = vi.fn()
    render(
      <ErrorCard
        message="Timeout"
        severity="retryable"
        actions={[
          { label: 'Retry', kind: 'retry' },
          { label: 'Simplify', kind: 'simplify' },
        ]}
        onAction={onAction}
      />,
    )
    fireEvent.click(screen.getByText('Retry'))
    expect(onAction).toHaveBeenCalledWith('retry')
  })

  it('hides action buttons when isResolved', () => {
    render(
      <ErrorCard
        message="Old error"
        severity="retryable"
        actions={[{ label: 'Retry', kind: 'retry' }]}
        isResolved
      />,
    )
    expect(screen.queryByText('Retry')).toBeNull()
  })

  it('toggles detail visibility', () => {
    render(
      <ErrorCard message="Error" detail="Stack trace here" severity="retryable" />,
    )
    expect(screen.queryByText('Stack trace here')).toBeNull()
    fireEvent.click(screen.getByText('Show details'))
    expect(screen.getByText('Stack trace here')).toBeTruthy()
  })

  it('renders critical with red icon', () => {
    render(<ErrorCard message="Fatal" severity="critical" />)
    expect(screen.getByText('🔴')).toBeTruthy()
  })

  it('renders actionable with blue icon', () => {
    render(<ErrorCard message="Fix it" severity="actionable" />)
    expect(screen.getByText('🔵')).toBeTruthy()
  })

  it('uses retryable as default severity', () => {
    render(<ErrorCard message="Something" />)
    expect(screen.getByText('🟡')).toBeTruthy()
  })
})

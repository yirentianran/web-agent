import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import Sidebar from '../components/Sidebar'
import type { SessionItem } from '../lib/types'

function renderSidebar() {
  const sessions: SessionItem[] = [
    { session_id: 's1', title: 'Test Session', status: 'completed' },
  ]
  return render(
    <Sidebar
      sessions={sessions}
      activeSession="s1"
      onSelect={() => {}}
      onNew={() => {}}
      onDelete={() => {}}
    />,
  )
}

describe('Sidebar - rendering', () => {
  it('renders the new session button', () => {
    renderSidebar()
    expect(screen.getByText('+ New Session')).toBeInTheDocument()
  })

  it('renders session titles', () => {
    renderSidebar()
    expect(screen.getByText('Test Session')).toBeInTheDocument()
  })

  it('shows empty state when no sessions', () => {
    render(
      <Sidebar
        sessions={[]}
        activeSession={null}
        onSelect={() => {}}
        onNew={() => {}}
      />,
    )
    expect(screen.getByText('No sessions yet')).toBeInTheDocument()
  })

  it('marks active session with filled dot', () => {
    const { container } = renderSidebar()
    expect(container.querySelector('.session-dot')?.textContent).toBe('●')
  })
})

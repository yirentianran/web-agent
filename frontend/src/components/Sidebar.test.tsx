import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import Sidebar from '../components/Sidebar'
import type { SessionItem } from '../lib/types'

function renderSidebar(props?: { onOpenFiles?: () => void; filesCount?: number }) {
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
      onOpenFiles={props?.onOpenFiles}
      filesCount={props?.filesCount}
    />,
  )
}

describe('Sidebar - Files button', () => {
  it('renders Files button when onOpenFiles is provided', () => {
    renderSidebar({ onOpenFiles: () => {} })
    expect(screen.getByText('Files')).toBeInTheDocument()
  })

  it('does not render Files button when onOpenFiles is not provided', () => {
    renderSidebar()
    expect(screen.queryByText('Files')).not.toBeInTheDocument()
  })

  it('shows files count badge', () => {
    renderSidebar({ onOpenFiles: () => {}, filesCount: 5 })
    expect(screen.getByText('5')).toBeInTheDocument()
  })
})

describe('Sidebar - Files button text alignment', () => {
  it('renders the Files label with the correct class for centering', () => {
    const { container } = renderSidebar({ onOpenFiles: () => {} })
    const label = container.querySelector('.sp-files-label')
    expect(label).not.toBeNull()
    expect(label!.textContent).toBe('Files')
    // CSS: .sp-files-label { text-align: center } centers the text
    // within the flex: 1 space between icon and count badge
  })
})

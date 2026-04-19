import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import UserMenu from '../components/UserMenu'

function renderUserMenu(props?: {
  userId?: string
  onOpenSettings?: () => void
  onOpenFeedback?: () => void
  onOpenEvolution?: () => void
  onOpenMCP?: () => void
  onLogout?: () => void
}) {
  return render(
    <UserMenu
      userId={props?.userId ?? 'test-user'}
      onOpenSettings={props?.onOpenSettings ?? (() => {})}
      onOpenFeedback={props?.onOpenFeedback ?? (() => {})}
      onOpenEvolution={props?.onOpenEvolution ?? (() => {})}
      onOpenMCP={props?.onOpenMCP ?? (() => {})}
      onLogout={props?.onLogout ?? (() => {})}
    />,
  )
}

describe('UserMenu - rendering', () => {
  it('renders user id as trigger text', () => {
    renderUserMenu({ userId: 'alice' })
    expect(screen.getByText('alice')).toBeInTheDocument()
  })

  it('shows a dropdown arrow in the trigger', () => {
    const { container } = renderUserMenu()
    expect(container.querySelector('.user-menu-chevron')).not.toBeNull()
  })

  it('does not show dropdown items initially', () => {
    renderUserMenu()
    expect(screen.queryByText('Skills Management')).not.toBeInTheDocument()
    expect(screen.queryByText('Skill Evolution')).not.toBeInTheDocument()
    expect(screen.queryByText('Logout')).not.toBeInTheDocument()
  })
})

describe('UserMenu - open/close', () => {
  it('toggles dropdown open on click', () => {
    renderUserMenu()
    fireEvent.click(screen.getByText('test-user'))
    expect(screen.getByText('Skills Management')).toBeInTheDocument()
    expect(screen.getByText('Skill Evolution')).toBeInTheDocument()
    expect(screen.getByText('Logout')).toBeInTheDocument()
  })

  it('toggles dropdown closed on second click', () => {
    renderUserMenu()
    fireEvent.click(screen.getByText('test-user'))
    expect(screen.getByText('Skills Management')).toBeInTheDocument()
    fireEvent.click(screen.getByText('test-user'))
    expect(screen.queryByText('Skills Management')).not.toBeInTheDocument()
  })

  it('closes dropdown when clicking outside', () => {
    renderUserMenu()
    fireEvent.click(screen.getByText('test-user'))
    expect(screen.getByText('Skills Management')).toBeInTheDocument()
    fireEvent.mouseDown(document.body)
    expect(screen.queryByText('Skills Management')).not.toBeInTheDocument()
  })

  it('does not close when clicking inside the dropdown', () => {
    renderUserMenu()
    fireEvent.click(screen.getByText('test-user'))
    expect(screen.getByText('Skills Management')).toBeInTheDocument()
    fireEvent.mouseDown(screen.getByText('Skills Management'))
    expect(screen.getByText('Skills Management')).toBeInTheDocument()
  })
})

describe('UserMenu - menu item order', () => {
  it('places MCP Servers below Skills Management', () => {
    renderUserMenu()
    fireEvent.click(screen.getByText('test-user'))
    const items = screen.getAllByRole('menuitem')
    const labels = items.map(el => el.textContent?.trim().slice(1)) // skip icon character
    // Expected order: Skills Management, MCP Servers, Feedback Management, Skill Evolution, Logout
    expect(labels[0]).toBe('Skills Management')
    expect(labels[1]).toBe('MCP Servers')
  })
})

describe('UserMenu - actions', () => {
  it('calls onOpenSettings when Settings is clicked', () => {
    const onOpenSettings = vi.fn()
    renderUserMenu({ onOpenSettings })
    fireEvent.click(screen.getByText('test-user'))
    fireEvent.click(screen.getByText('Skills Management'))
    expect(onOpenSettings).toHaveBeenCalledTimes(1)
  })

  it('calls onLogout when Logout is clicked', () => {
    const onLogout = vi.fn()
    renderUserMenu({ onLogout })
    fireEvent.click(screen.getByText('test-user'))
    fireEvent.click(screen.getByText('Logout'))
    expect(onLogout).toHaveBeenCalledTimes(1)
  })

  it('closes dropdown after selecting Settings', () => {
    const onOpenSettings = vi.fn()
    renderUserMenu({ onOpenSettings })
    fireEvent.click(screen.getByText('test-user'))
    fireEvent.click(screen.getByText('Skills Management'))
    expect(screen.queryByText('Skills Management')).not.toBeInTheDocument()
  })

  it('closes dropdown after selecting Logout', () => {
    const onLogout = vi.fn()
    renderUserMenu({ onLogout })
    fireEvent.click(screen.getByText('test-user'))
    fireEvent.click(screen.getByText('Logout'))
    expect(screen.queryByText('Logout')).not.toBeInTheDocument()
  })

  it('calls onOpenEvolution when Skill Evolution is clicked', () => {
    const onOpenEvolution = vi.fn()
    renderUserMenu({ onOpenEvolution })
    fireEvent.click(screen.getByText('test-user'))
    fireEvent.click(screen.getByText('Skill Evolution'))
    expect(onOpenEvolution).toHaveBeenCalledTimes(1)
  })
})

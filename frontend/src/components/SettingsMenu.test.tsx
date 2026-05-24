import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import SettingsMenu from '../components/SettingsMenu'

function renderSettingsMenu(props?: {
  onOpenSkills?: () => void
  onOpenEvolution?: () => void
  onOpenMCP?: () => void
  onOpenDashboard?: () => void
  userRole?: string
}) {
  return render(
    <SettingsMenu
      onOpenSkills={props?.onOpenSkills ?? (() => {})}
      onOpenEvolution={props?.onOpenEvolution ?? (() => {})}
      onOpenMCP={props?.onOpenMCP ?? (() => {})}
      onOpenDashboard={props?.onOpenDashboard ?? (() => {})}
      userRole={props?.userRole ?? "admin"}
    />,
  )
}

describe('SettingsMenu - rendering', () => {
  it('renders settings icon button', () => {
    const { container } = renderSettingsMenu()
    expect(container.querySelector('.settings-menu-trigger')).not.toBeNull()
  })

  it('does not show dropdown items initially', () => {
    renderSettingsMenu()
    expect(screen.queryByText('Skills Management')).not.toBeInTheDocument()
    expect(screen.queryByText('MCP Servers')).not.toBeInTheDocument()
  })
})

describe('SettingsMenu - open/close', () => {
  it('toggles dropdown open on click', () => {
    renderSettingsMenu()
    const trigger = document.querySelector('.settings-menu-trigger') as HTMLElement
    fireEvent.click(trigger)
    expect(screen.getByText('Skills Management')).toBeInTheDocument()
    expect(screen.getByText('MCP Servers')).toBeInTheDocument()
  })

  it('toggles dropdown closed on second click', () => {
    renderSettingsMenu()
    const trigger = document.querySelector('.settings-menu-trigger') as HTMLElement
    fireEvent.click(trigger)
    expect(screen.getByText('Skills Management')).toBeInTheDocument()
    fireEvent.click(trigger)
    expect(screen.queryByText('Skills Management')).not.toBeInTheDocument()
  })

  it('closes dropdown when clicking outside', () => {
    renderSettingsMenu()
    const trigger = document.querySelector('.settings-menu-trigger') as HTMLElement
    fireEvent.click(trigger)
    expect(screen.getByText('Skills Management')).toBeInTheDocument()
    fireEvent.mouseDown(document.body)
    expect(screen.queryByText('Skills Management')).not.toBeInTheDocument()
  })
})

describe('SettingsMenu - actions', () => {
  it('calls onOpenSkills when Skills Management is clicked', () => {
    const onOpenSkills = vi.fn()
    renderSettingsMenu({ onOpenSkills })
    const trigger = document.querySelector('.settings-menu-trigger') as HTMLElement
    fireEvent.click(trigger)
    fireEvent.click(screen.getByText('Skills Management'))
    expect(onOpenSkills).toHaveBeenCalledTimes(1)
  })

  it('calls onOpenMCP when MCP Servers is clicked', () => {
    const onOpenMCP = vi.fn()
    renderSettingsMenu({ onOpenMCP })
    const trigger = document.querySelector('.settings-menu-trigger') as HTMLElement
    fireEvent.click(trigger)
    fireEvent.click(screen.getByText('MCP Servers'))
    expect(onOpenMCP).toHaveBeenCalledTimes(1)
  })

  it('closes dropdown after selecting an item', () => {
    const onOpenSkills = vi.fn()
    renderSettingsMenu({ onOpenSkills })
    const trigger = document.querySelector('.settings-menu-trigger') as HTMLElement
    fireEvent.click(trigger)
    fireEvent.click(screen.getByText('Skills Management'))
    expect(screen.queryByText('Skills Management')).not.toBeInTheDocument()
  })

  it('places menu items in correct order', () => {
    renderSettingsMenu()
    const trigger = document.querySelector('.settings-menu-trigger') as HTMLElement
    fireEvent.click(trigger)
    const items = screen.getAllByRole('menuitem')
    const labels = items.map(el => el.textContent?.trim().replace(/\p{Emoji_Presentation}/gu, '').trim())
    expect(labels[0]).toBe('Skills Management')
    expect(labels).toContain('Usage Dashboard')
    expect(labels).toContain('MCP Servers')
  })
})

describe('SettingsMenu - admin role filtering', () => {
  it('shows admin items for admin users', () => {
    renderSettingsMenu({ userRole: 'admin' })
    const trigger = document.querySelector('.settings-menu-trigger') as HTMLElement
    fireEvent.click(trigger)
    expect(screen.getByText('MCP Servers')).toBeInTheDocument()
    expect(screen.getByText('Skill Evolution')).toBeInTheDocument()
  })

  it('hides admin items for non-admin users', () => {
    renderSettingsMenu({ userRole: 'user' })
    const trigger = document.querySelector('.settings-menu-trigger') as HTMLElement
    fireEvent.click(trigger)
    expect(screen.queryByText('MCP Servers')).not.toBeInTheDocument()
    expect(screen.queryByText('Skill Evolution')).not.toBeInTheDocument()
  })

  it('shows Skills Management for all users', () => {
    renderSettingsMenu({ userRole: 'user' })
    const trigger = document.querySelector('.settings-menu-trigger') as HTMLElement
    fireEvent.click(trigger)
    expect(screen.getByText('Skills Management')).toBeInTheDocument()
  })
})

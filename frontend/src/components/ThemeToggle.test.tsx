import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import ThemeToggle from './ThemeToggle'

const mockToggleTheme = vi.fn()

vi.mock('../hooks/useTheme', () => ({
  useTheme: () => ({
    theme: 'light',
    toggleTheme: mockToggleTheme,
    setTheme: vi.fn(),
  }),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        'theme.switchToDark': 'Switch to dark mode',
        'theme.switchToLight': 'Switch to light mode',
      }
      return map[key] || key
    },
  }),
}))

describe('ThemeToggle', () => {
  beforeEach(() => {
    mockToggleTheme.mockClear()
  })

  it('renders with moon icon in light mode', () => {
    render(<ThemeToggle />)
    expect(screen.getByRole('button')).toBeInTheDocument()
    expect(screen.getByLabelText('Switch to dark mode')).toBeInTheDocument()
  })

  it('calls toggleTheme on click', () => {
    render(<ThemeToggle />)
    fireEvent.click(screen.getByRole('button'))
    expect(mockToggleTheme).toHaveBeenCalledOnce()
  })
})

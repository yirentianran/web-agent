import { createContext, useEffect, useState, useCallback, useMemo } from 'react'

type Theme = 'light' | 'dark'
type ThemeSource = 'system' | 'manual'

interface ThemeContextValue {
  theme: Theme
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

function getSystemTheme(): Theme {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme
}

function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => {
    const pref = localStorage.getItem('theme-preference')
    if (pref === 'dark' || pref === 'light') return pref
    return getSystemTheme()
  })

  const [source, setSource] = useState<ThemeSource>(() => {
    return localStorage.getItem('theme-source') as ThemeSource || 'system'
  })

  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  useEffect(() => {
    if (source !== 'system') return

    const mql = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = (e: MediaQueryListEvent) => {
      setThemeState(e.matches ? 'dark' : 'light')
    }
    mql.addEventListener('change', handler)
    return () => mql.removeEventListener('change', handler)
  }, [source])

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t)
    setSource('manual')
    localStorage.setItem('theme-preference', t)
    localStorage.setItem('theme-source', 'manual')
  }, [])

  const toggleTheme = useCallback(() => {
    setThemeState(prev => {
      const next = prev === 'light' ? 'dark' : 'light'
      localStorage.setItem('theme-preference', next)
      localStorage.setItem('theme-source', 'manual')
      setSource('manual')
      return next
    })
  }, [])

  const value = useMemo(() => ({ theme, setTheme, toggleTheme }), [theme, setTheme, toggleTheme])

  return (
    <ThemeContext value={value}>
      {children}
    </ThemeContext>
  )
}

export { ThemeContext, ThemeProvider }
export type { Theme, ThemeContextValue }

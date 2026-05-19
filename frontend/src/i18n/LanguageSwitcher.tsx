import { useTranslation } from 'react-i18next'

interface LanguageSwitcherProps {
  userId?: string
  authToken?: string | null
}

export default function LanguageSwitcher({ userId, authToken }: LanguageSwitcherProps) {
  const { i18n } = useTranslation()
  const currentLng = i18n.language

  const syncLanguageToBackend = (language: string) => {
    if (!userId) return
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (authToken) headers['Authorization'] = `Bearer ${authToken}`
    fetch(`/api/users/${userId}/language`, {
      method: 'PUT',
      headers,
      body: JSON.stringify({ language }),
    }).catch((err) => {
      console.error('[LanguageSwitcher] Failed to sync language to backend:', err)
    })
  }

  const toggleLanguage = () => {
    const next = currentLng === 'zh' ? 'en' : 'zh'
    i18n.changeLanguage(next)
    syncLanguageToBackend(next)
  }

  return (
    <button
      className="language-switcher"
      onClick={toggleLanguage}
      type="button"
      title={currentLng === 'zh' ? 'Switch to English' : '切换中文'}
    >
      {currentLng === 'zh' ? 'EN' : '中'}
    </button>
  )
}

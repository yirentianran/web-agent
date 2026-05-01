import { useTranslation } from 'react-i18next'

export default function LanguageSwitcher() {
  const { i18n } = useTranslation()
  const currentLng = i18n.language

  const toggleLanguage = () => {
    const next = currentLng === 'zh' ? 'en' : 'zh'
    i18n.changeLanguage(next)
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
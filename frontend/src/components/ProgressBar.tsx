import { useTranslation } from 'react-i18next'
import type { Message } from '../lib/types'

export type Phase = 'analyze' | 'edit' | 'verify' | 'working'

interface PhaseInfo {
  key: Phase
  labelKey: string
  icon: string
}

const PHASES: PhaseInfo[] = [
  { key: 'analyze', labelKey: 'progress.analyze', icon: '🔍' },
  { key: 'edit', labelKey: 'progress.edit', icon: '✏️' },
  { key: 'verify', labelKey: 'progress.verify', icon: '✓' },
]

interface ProgressBarProps {
  currentPhase: Phase
  visible: boolean
}

export default function ProgressBar({ currentPhase, visible }: ProgressBarProps) {
  const { t } = useTranslation()

  if (!visible) return null

  const currentIdx = PHASES.findIndex(p => p.key === currentPhase)

  return (
    <div className="progress-bar" role="status" aria-label={t('progress.label')}>
      {PHASES.map((phase, i) => {
        let status: 'done' | 'active' | 'pending'
        if (i < currentIdx) status = 'done'
        else if (i === currentIdx) status = 'active'
        else status = 'pending'

        return (
          <div key={phase.key} className={`progress-step progress-step--${status}`}>
            <span className="progress-step__icon" aria-hidden="true">
              {status === 'done' ? '✅' : phase.icon}
            </span>
            <span className="progress-step__label">{t(phase.labelKey)}</span>
            {i < PHASES.length - 1 && (
              <span className="progress-step__arrow" aria-hidden="true">→</span>
            )}
          </div>
        )
      })}
    </div>
  )
}

/**
 * Detect the agent's current phase from message history.
 *
 * Only considers messages from the *current* agent turn (since the last
 * user message). This avoids stale phase detection from prior turns.
 *
 * Heuristic:
 *  - No tool_use messages → 'analyze' (initial state)
 *  - Only read/search tool calls so far → 'analyze'
 *  - First Write/Edit observed → 'edit'
 *  - First Bash observed (after Write/Edit) → 'verify'
 *  - Otherwise → 'working' (fallback)
 */
export function detectPhase(messages: Message[]): Phase {
  // Only consider tool_use messages from the current agent turn
  const toolUses: Message[] = []
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (m.type === 'user') break
    if (m.type === 'tool_use') toolUses.unshift(m)
  }

  if (toolUses.length === 0) return 'analyze'

  // Skip TodoWrite — it's internal bookkeeping, not user-visible work
  const workTools = toolUses.filter(m => m.name !== 'TodoWrite')

  const readTools = new Set(['Read', 'Grep', 'Glob', 'WebSearch', 'WebFetch'])
  let hasEdit = false
  let hasVerify = false

  for (const msg of workTools) {
    const name = msg.name || ''
    if (name === 'Write' || name === 'Edit') {
      hasEdit = true
    }
    if (name === 'Bash' && hasEdit) {
      hasVerify = true
    }
  }

  if (hasVerify) return 'verify'
  if (hasEdit) return 'edit'

  // Check if ALL work tools are read-type
  const allReads = workTools.every(m => readTools.has(m.name || ''))
  if (allReads) return 'analyze'

  return 'working'
}

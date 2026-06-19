import { useTranslation } from 'react-i18next'
import type { Message } from '../lib/types'

export type Phase = 'analyze' | 'edit' | 'verify'

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

export interface ToolCounts {
  analyze: number
  edit: number
  verify: number
}

interface ProgressBarProps {
  currentPhase: Phase
  visible: boolean
  toolCounts?: ToolCounts
}

export default function ProgressBar({ currentPhase, visible, toolCounts }: ProgressBarProps) {
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

        const count = toolCounts?.[phase.key as keyof ToolCounts]
        const label = count != null && count > 0
          ? `${t(phase.labelKey)} · ${count}`
          : t(phase.labelKey)

        return (
          <div key={phase.key} className={`progress-step progress-step--${status}`}>
            <span className="progress-step__icon" aria-hidden="true">
              {status === 'done' ? '✅' : phase.icon}
            </span>
            <span className="progress-step__label">{label}</span>
            {i < PHASES.length - 1 && (
              <span className="progress-step__arrow" aria-hidden="true">→</span>
            )}
          </div>
        )
      })}
    </div>
  )
}

// Tools whose calls are internal bookkeeping, not user-visible work.
const SKIP_TOOLS = new Set(['TodoWrite', 'AskUserQuestion'])

// Tools that mutate files — signal the "edit" phase.
const EDIT_TOOLS = new Set(['Write', 'Edit'])

/**
 * Count how many current-turn tool calls fall into each phase bucket.
 * Only scans messages since the last user message.
 */
export function computeToolCounts(messages: Message[]): ToolCounts {
  const toolUses: Message[] = []
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (m.type === 'user') break
    if (m.type === 'tool_use' && !SKIP_TOOLS.has(m.name || '')) toolUses.unshift(m)
  }

  return {
    analyze: toolUses.filter(m => !EDIT_TOOLS.has(m.name || '') && m.name !== 'Bash').length,
    edit: toolUses.filter(m => EDIT_TOOLS.has(m.name || '')).length,
    verify: toolUses.filter(m => m.name === 'Bash').length,
  }
}

/**
 * Detect the agent's current phase from the current turn's tool calls.
 *
 * Only scans messages since the last user message so each conversation
 * turn gets a fresh progression. Unknown tools (MCP, Skill, Agent, …)
 * are treated as part of the analyze phase — the bar never hides because
 * of an unrecognized tool name.
 */
export function detectPhase(messages: Message[]): Phase {
  // Collect tool_use messages from the current turn only
  const toolUses: Message[] = []
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (m.type === 'user') break
    if (m.type === 'tool_use') toolUses.unshift(m)
  }

  if (toolUses.length === 0) return 'analyze'

  const workTools = toolUses.filter(m => !SKIP_TOOLS.has(m.name || ''))

  let hasEdit = false
  let hasVerify = false

  for (const msg of workTools) {
    const name = msg.name || ''
    if (EDIT_TOOLS.has(name)) hasEdit = true
    if (name === 'Bash') hasVerify = true
  }

  if (hasVerify) return 'verify'
  if (hasEdit) return 'edit'
  // Everything else — Read, Grep, Glob, WebSearch, WebFetch, Agent,
  // Skill, MCP tools, unknown — is analysis work.
  return 'analyze'
}

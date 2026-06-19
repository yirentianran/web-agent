import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import ToolCard, { getToolIcon, buildToolSummary, fmtDuration } from './ToolCard'
import type { Message } from '../lib/types'

interface ToolGroupRendererProps {
  tools: Message[]
}

/** Merge threshold: group when 3+ consecutive same-tool calls appear. */
const MERGE_THRESHOLD = 3

export default function ToolGroupRenderer({ tools }: ToolGroupRendererProps) {
  const { t, i18n } = useTranslation()
  const [expanded, setExpanded] = useState(false)

  if (tools.length === 0) return null

  const name = tools[0].name || 'unknown'
  const totalDuration = tools.reduce(
    (sum, t) => sum + (t.duration_ms || 0), 0,
  )

  // Extract summary info for the first few items
  const previewItems = tools.slice(0, 3).map((t) => {
    const input = t.input as Record<string, unknown> | undefined
    if (name === 'Read' || name === 'Write' || name === 'Edit') {
      if (input && 'file_path' in input) return String(input.file_path)
      if (input && 'path' in input) return String(input.path)
    }
    return buildToolSummary(name, input || {}) || ''
  })

  const remaining = tools.length - previewItems.length

  return (
    <details className="message tool-group" open={expanded}>
      <summary
        className="tool-group__summary"
        onClick={(e) => {
          e.preventDefault()
          setExpanded(!expanded)
        }}
      >
        <span className="tool-icon">{getToolIcon(name)}</span>
        <span className="tool-name">{name}</span>
        <span className="tool-detail">
          {tools.length} {t('message.toolCalls')}
        </span>
        {totalDuration > 0 && (
          <span className="tool-duration">
            {' '}
            ⏱ {fmtDuration(totalDuration, i18n.language)}
          </span>
        )}
      </summary>

      <div className="tool-group__preview">
        {previewItems.map((item, idx) => (
          <div key={idx} className="tool-group__item">
            {item}
          </div>
        ))}
        {remaining > 0 && (
          <div className="tool-group__more">
            {t('message.andMore', { count: remaining })}
          </div>
        )}
      </div>

      {expanded && (
        <div className="tool-group__expanded">
          {tools.map((tool, idx) => {
            const input = tool.input as Record<string, unknown> | undefined
            const summary = input ? buildToolSummary(name, input) : ''
            return (
              <ToolCard key={idx} name={name} summary={summary} toolResult={tool.toolResult}>
                <pre className="tool-content">
                  {JSON.stringify(tool.input, null, 2)}
                </pre>
              </ToolCard>
            )
          })}
        </div>
      )}
    </details>
  )
}

/**
 * Given a list of messages, merge consecutive same-tool calls into groups.
 * Returns a new list where grouped tools are replaced by a single marker message.
 */
export function groupConsecutiveTools(messages: Message[]): Message[] {
  const result: Message[] = []
  let i = 0

  while (i < messages.length) {
    const msg = messages[i]

    // Only merge tool_use messages
    if (msg.type !== 'tool_use') {
      result.push(msg)
      i++
      continue
    }

    // Start collecting consecutive same-tool calls
    const toolName = msg.name
    const group: Message[] = [msg]
    i++

    while (
      i < messages.length &&
      messages[i].type === 'tool_use' &&
      messages[i].name === toolName
    ) {
      group.push(messages[i])
      i++
    }

    if (group.length >= MERGE_THRESHOLD) {
      result.push({
        type: 'tool_use',
        name: toolName,
        content: '',
        index: group[0].index,
        input: { _grouped: true, _tools: group } as unknown,
        duration_ms: group.reduce((sum, t) => sum + (t.duration_ms || 0), 0),
      } as Message)
    } else {
      result.push(...group)
    }
  }

  return result
}

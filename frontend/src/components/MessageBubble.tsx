import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Message } from '../lib/types'
import MarkdownRenderer from './MarkdownRenderer'
import { FileCardList } from './FileCards'
import AskUserQuestionCard from './AskUserQuestionCard'
import TodoWriteViz from './TodoWriteViz'
import { parseTodoWriteInput } from '../lib/todos'


// ── Filename validation ──────────────────────────────────────────

const INVALID_FILENAMES = new Set(['null', 'undefined'])

function isValidFilename(name: string | undefined | null): boolean {
  if (!name) return false
  if (INVALID_FILENAMES.has(name.toLowerCase())) return false
  return true
}

// ── Tool display helpers ─────────────────────────────────────────

const DISABLED_TOOLS = ['WebSearch', 'WebFetch'] as const

// Only these system subtypes are user-visible; everything else is internal
const VISIBLE_SYSTEM_SUBTYPES = new Set<string>([])

const TOOL_ICONS: Record<string, string> = {
  Read: '📖',
  Write: '✏️',
  Edit: '✏️',
  Bash: '⌨️',
  Glob: '🔍',
  Grep: '🔎',
  WebFetch: '🌐',
  WebSearch: '🔍',
  Agent: '🤖',
  Skill: '',
}

const DANGER_TOOLS = new Set(['Bash', 'Write', 'Edit'])

/** Pair tool_use with tool_result messages — merges results into their parent tool_use cards. */
export function pairToolMessages(messages: Message[]): Message[] {
  // Collect tool_use ids first so we only pair results that have a matching tool_use
  const toolUseIds = new Set<string>()
  for (const msg of messages) {
    if (msg.type === 'tool_use' && msg.id) {
      toolUseIds.add(msg.id)
    }
  }

  const resultMap = new Map<string, Message>()
  for (const msg of messages) {
    if (msg.type === 'tool_result' && msg.tool_use_id && toolUseIds.has(msg.tool_use_id)) {
      resultMap.set(msg.tool_use_id, msg)
    }
  }

  return messages
    .filter(msg => !(msg.type === 'tool_result' && msg.tool_use_id && resultMap.has(msg.tool_use_id)))
    .map(msg => {
      if (msg.type === 'tool_use' && msg.id && msg.name !== 'AskUserQuestion') {
        const result = resultMap.get(msg.id)
        if (result) {
          return {
            ...msg,
            toolResult: {
              content: result.content || '',
              is_error: result.is_error,
              name: result.name,
            },
          }
        }
      }
      return msg
    })
}

function getToolIcon(name?: string): string {
  return TOOL_ICONS[name || ''] || '🔧'
}

function buildToolSummary(name: string | undefined, input: Record<string, unknown>): string {
  if (name === 'Bash') {
    const cmd = String(input.command || '')
    return cmd.length > 60 ? cmd.slice(0, 57) + '…' : cmd
  }
  if (name === 'Read' || name === 'Glob' || name === 'Grep') {
    return String(input.path || input.glob || input.pattern || input.query || '')
  }
  if (name === 'Write') {
    return String(input.file_path || '')
  }
  if (name === 'Edit') {
    return String(input.file_path || '')
  }
  if (DISABLED_TOOLS.includes(name as typeof DISABLED_TOOLS[number])) {
    return String(input.query ?? input.url ?? '')
  }
  if (name === 'Agent') {
    return String(input.prompt || input.description || '')
  }
  return ''
}

// ── Bash command formatter ───────────────────────────────────────

export interface FormattedBashCommand {
  command: string
  description: string | null
}

export function formatBashCommand(input: Record<string, unknown>): FormattedBashCommand {
  const rawCmd = String(input.command ?? '')
  // Unescape JSON \n and \r\n to real newlines for display; leave \t and others alone
  const command = rawCmd.replace(/\\r\\n/g, '\r\n').replace(/\\n/g, '\n')
  const descriptionRaw = input.description
  const description = descriptionRaw !== undefined ? String(descriptionRaw) : null
  return { command, description }
}

// ── File content formatter (Write / Edit) ────────────────────────

export interface FormattedFileContent {
  content: string
  filePath: string | null
}

export function formatFileContent(input: Record<string, unknown>): FormattedFileContent {
  const rawContent = String(input.content ?? '')
  const content = rawContent.replace(/\\r\\n/g, '\r\n').replace(/\\n/g, '\n')
  const filePathRaw = input.file_path
  const filePath = filePathRaw !== undefined ? String(filePathRaw) : null
  return { content, filePath }
}

export interface FormattedEditContent {
  filePath: string | null
  oldContent: string
  newContent: string
}

function formatEditContent(input: Record<string, unknown>): FormattedEditContent {
  const filePath = input.file_path ? String(input.file_path) : null
  const oldStr = String(input.old_string ?? '')
  const newStr = String(input.new_string ?? '')
  return {
    filePath,
    oldContent: oldStr.replace(/\\r\\n/g, '\r\n').replace(/\\n/g, '\n'),
    newContent: newStr.replace(/\\r\\n/g, '\r\n').replace(/\\n/g, '\n'),
  }
}

// ── Analysis / Summary tag parser ────────────────────────────────

export type TagBlock = { kind: 'analysis' | 'summary' | 'text'; content: string }

export function parseTagBlocks(text: string): TagBlock[] {
  const parts: TagBlock[] = []
  const pattern = /<(analysis|summary)>([\s\S]*?)<\/\1>/g

  let lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      const trimmed = text.slice(lastIndex, match.index).trim()
      if (trimmed) {
        parts.push({ kind: 'text', content: trimmed })
      }
    }
    const tagContent = match[2].trim()
    if (tagContent) {
      parts.push({ kind: match[1] as 'analysis' | 'summary', content: tagContent })
    }
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) {
    const trimmed = text.slice(lastIndex).trim()
    if (trimmed) {
      parts.push({ kind: 'text', content: trimmed })
    }
  }
  if (parts.length === 0) {
    return [{ kind: 'text', content: text }]
  }
  return parts
}

export function hasIncompleteTag(text: string): boolean {
  const openAnalysis = (text.match(/<analysis>/g) || []).length
  const closeAnalysis = (text.match(/<\/analysis>/g) || []).length
  const openSummary = (text.match(/<summary>/g) || []).length
  const closeSummary = (text.match(/<\/summary>/g) || []).length
  return openAnalysis !== closeAnalysis || openSummary !== closeSummary
}

type BlockKind = 'thinking' | 'analysis' | 'summary' | 'text'
interface BlockPart { kind: BlockKind; content: string }

function parseAssistantContent(text: string): BlockPart[] {
  const parts: BlockPart[] = []
  const pattern = /\[thinking\]([\s\S]*?)\[\/thinking\]|<(analysis|summary)>([\s\S]*?)<\/\2>/g
  let lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      const before = text.slice(lastIndex, match.index).trim()
      if (before) parts.push({ kind: 'text', content: before })
    }
    if (match[1] !== undefined) {
      const thinking = match[1].trim()
      if (thinking) parts.push({ kind: 'thinking', content: thinking })
    } else {
      const kind = match[2] as 'analysis' | 'summary'
      const content = match[3].trim()
      if (content) parts.push({ kind, content })
    }
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) {
    const remaining = text.slice(lastIndex).trim()
    if (remaining) parts.push({ kind: 'text', content: remaining })
  }
  return parts
}

interface MessageBubbleProps {
  message: Message
  sessionId: string
  onAnswer: (sessionId: string, answers: Record<string, string>) => void
  onFileClick?: (filename: string) => void
  onResend?: (message: Message) => void
  lastTodoWriteIndex?: number
  lastUserMsgIndex?: number
  authToken?: string | null
}

const COLLAPSE_THRESHOLD = 5000

function formatJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

function ToolResultContent({ content, isJson }: { content: string; isJson: boolean }) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const displayContent = isJson ? formatJson(content) : content
  const isLarge = displayContent.length > COLLAPSE_THRESHOLD
  const visibleContent = isLarge && !expanded ? displayContent.slice(0, COLLAPSE_THRESHOLD) + '…' : displayContent

  return (
    <>
      {isJson ? (
        <pre className={`tool-output tool-output-json${isLarge && !expanded ? ' tool-output-collapsed' : ''}`}><code>{visibleContent}</code></pre>
      ) : (
        <div className={`tool-output-markdown${isLarge && !expanded ? ' tool-output-collapsed' : ''}`}>
          <MarkdownRenderer>{visibleContent}</MarkdownRenderer>
        </div>
      )}
      {isLarge && (
        <button className="tool-output-expand-btn" onClick={() => setExpanded(e => !e)}>
          {expanded ? t('message.collapse') : t('message.showAll')}
        </button>
      )}
    </>
  )
}

function ToolResultSection({ toolResult }: { toolResult?: Message['toolResult'] }) {
  const { t } = useTranslation()

  if (!toolResult) {
    return (
      <div className="tool-result-section tool-result-section--running">
        <div className="tool-result-separator" />
        <div className="tool-result-header">
          <span className="tool-result-icon tool-result-icon--running">⏳</span>
          <span className="tool-result-status">{t('message.toolRunning')}</span>
        </div>
      </div>
    )
  }

  const content = toolResult.content || ''
  const isEmpty = !content && !toolResult.is_error
  const isJson = /^\s*[{[]/.test(content)

  return (
    <div className={`tool-result-section${toolResult.is_error ? ' tool-result-section--error' : ''}`}>
      <div className="tool-result-separator" />
      <div className="tool-result-header">
        <span className="tool-result-icon">
          {toolResult.is_error ? '✗' : '✓'}
        </span>
        <span className={`tool-result-status${toolResult.is_error ? ' tool-result-status--error' : ''}`}>
          {toolResult.is_error
            ? t('message.errorOccurred')
            : t('message.result')}
        </span>
      </div>
      {isEmpty ? (
        <div className="tool-result-empty">{t('message.resultEmpty')}</div>
      ) : (
        <ToolResultContent content={content} isJson={isJson} />
      )}
    </div>
  )
}

export default function MessageBubble({ message, sessionId, onAnswer, onFileClick, onResend, lastTodoWriteIndex, lastUserMsgIndex, authToken }: MessageBubbleProps) {
  const { t } = useTranslation()
  if (message.type === 'user') {
    const files = (message.data as Array<{ filename: string; size?: number }> | undefined) || []
    if ((!message.content || !message.content.trim()) && files.length === 0) return null
    const handleFileClick = onFileClick
      ? (filename: string) => { onFileClick(filename) }
      : undefined

    // Send state indicator
    const sendStateIcon = message.sendState === 'sending'
      ? <span className="send-state send-state--sending" title={t('message.sending')} aria-label={t('message.sending')} />
      : message.sendState === 'failed'
      ? <span className="send-state send-state--failed" title={t('message.sendFailed')} aria-label={t('message.sendFailed')} role="button" tabIndex={0} onClick={() => onResend?.(message)}>✗</span>
      : null

    return (
      <>
        {files.length > 0 && files.map((f, i) => (
          <div key={i} className="message user-file-message">
            <FileCardList
              files={[{ ...f, downloadUrl: undefined }]}
              status="uploaded"
              onFileClick={handleFileClick}
            />
          </div>
        ))}
        {message.content && message.content.trim() && (
          <div className="message user-message">
            <div className="bubble">
              {sendStateIcon && <span className="send-state-wrapper">{sendStateIcon}</span>}
              <MarkdownRenderer>{message.content}</MarkdownRenderer>
            </div>
          </div>
        )}
      </>
    )
  }

  if (message.type === 'system') {
    // Only show explicitly whitelisted subtypes; hide everything else
    // to prevent internal SDK messages from leaking into the chat.
    const subtype = message.subtype || ''
    if (!VISIBLE_SYSTEM_SUBTYPES.has(subtype)) return null
    const displayText = message.content
      || (message.subtype ? `[${message.subtype}]` : '')
      || (message.data ? JSON.stringify(message.data) : '')
    return (
      <div className="message system-message">
        <span className="system-text">{displayText}</span>
      </div>
    )
  }

  if (message.type === 'tool_use') {
    if (message.name === 'AskUserQuestion') {
      return (
        <div className="message question-message">
          <AskUserQuestionCard
            input={message.input as any}
            sessionId={sessionId}
            onAnswer={onAnswer}
          />
        </div>
      )
    }
    if (message.name === 'TodoWrite') {
      const todos = parseTodoWriteInput(message.input)
      if (todos && todos.length > 0) {
        // Only show the latest TodoWrite; hide older ones
        if (lastTodoWriteIndex !== undefined && message.index < lastTodoWriteIndex) {
          return null
        }
        return <TodoWriteViz todos={todos} />
      }
    }
    const input = message.input as Record<string, unknown> | undefined
    const summary = input ? buildToolSummary(message.name, input) : ''

    // Bash tool_use: show description + formatted command instead of raw JSON
    if (message.name === 'Bash') {
      if (!input) return null
      const { command, description } = formatBashCommand(input)
      if (!command) return null
      const isDanger = DANGER_TOOLS.has(message.name || '')
      return (
        <details
          className={`message tool-message${isDanger ? ' tool-message--danger' : ''}`}
          open={false}
        >
          <summary className="tool-summary">
            <span className="tool-icon">{getToolIcon(message.name)}</span>
            <span className="tool-name">{message.name}</span>
            {summary && <span className="tool-detail">{summary}</span>}
          </summary>
          {description && <div className="tool-description">{description}</div>}
          <pre className="tool-input"><code>{command}</code></pre>
          <ToolResultSection toolResult={message.toolResult} />
        </details>
      )
    }

    // Write tool_use: show file path + content
    if (message.name === 'Write' && input) {
      const { content, filePath } = formatFileContent(input)
      const isDanger = DANGER_TOOLS.has('Write')
      return (
        <details
          className={`message tool-message${isDanger ? ' tool-message--danger' : ''}`}
          open={false}
        >
          <summary className="tool-summary">
            <span className="tool-icon">{getToolIcon(message.name)}</span>
            <span className="tool-name">{message.name}</span>
            {summary && <span className="tool-detail">{summary}</span>}
          </summary>
          {filePath && <div className="tool-description">{filePath}</div>}
          <pre className="tool-input"><code>{content}</code></pre>
          <ToolResultSection toolResult={message.toolResult} />
        </details>
      )
    }

    // Edit tool_use: show old_string → new_string diff
    if (message.name === 'Edit' && input) {
      const { filePath, oldContent, newContent } = formatEditContent(input)
      const isDanger = DANGER_TOOLS.has('Edit')
      return (
        <details
          className={`message tool-message${isDanger ? ' tool-message--danger' : ''}`}
          open={false}
        >
          <summary className="tool-summary">
            <span className="tool-icon">{getToolIcon(message.name)}</span>
            <span className="tool-name">{message.name}</span>
            {filePath && <span className="tool-detail">{filePath}</span>}
          </summary>
          {filePath && <div className="tool-description">{filePath}</div>}
          <div className="tool-edit-content">
            <div className="tool-edit-old">
              <span className="tool-edit-label">{t('message.removed')}</span>
              <pre><code>{oldContent || t('message.none')}</code></pre>
            </div>
            <div className="tool-edit-new">
              <span className="tool-edit-label">{t('message.added')}</span>
              <pre><code>{newContent || t('message.none')}</code></pre>
            </div>
          </div>
          <ToolResultSection toolResult={message.toolResult} />
        </details>
      )
    }

    const isGenericDanger = DANGER_TOOLS.has(message.name || '')
    return (
      <details
        className={`message tool-message${isGenericDanger ? ' tool-message--danger' : ''}`}
        open={false}
      >
        <summary className="tool-summary">
          <span className="tool-icon">{getToolIcon(message.name)}</span>
          <span className="tool-name">{message.name || 'unknown'}</span>
          {summary && <span className="tool-detail">{summary}</span>}
        </summary>
        <pre className="tool-input">{JSON.stringify(message.input, null, 2)}</pre>
        <ToolResultSection toolResult={message.toolResult} />
      </details>
    )
  }

  if (message.type === 'tool_result') {
    const rawContent = message.content || ''
    // Hide empty tool results (e.g., TaskOutput with no content) unless it's an error
    const isEmpty = !rawContent && !message.is_error
    const displayContent = rawContent || (message.is_error ? t('message.toolErrorNoOutput') : (isEmpty ? t('message.resultEmpty') : ''))
    const isJson = /^\s*[{[]/.test(rawContent)
    const isResolved = message.is_error && lastUserMsgIndex !== undefined && lastUserMsgIndex > message.index

    // Empty success result — show non-interactive indicator, no details to expand
    if (isEmpty) {
      return (
        <div className="message tool-result tool-result--empty">
          <span className="tool-summary">
            <span className="tool-icon">{getToolIcon(message.name)}</span>
            <span className="tool-name">{message.name || 'unknown'}</span>
            <span className="tool-detail">{t('message.resultEmpty')}</span>
          </span>
        </div>
      )
    }

    return (
      <details
        className={`message tool-result${message.is_error ? ' tool-result--error' : ''}${isResolved ? ' tool-result--resolved' : ''}`}
        open={message.is_error ? true : undefined}
      >
        <summary className="tool-summary">
          <span className="tool-icon">{getToolIcon(message.name)}</span>
          <span className="tool-name">{message.name || 'unknown'}</span>
          <span className="tool-detail">{t('message.result')}{isResolved ? ` ${t('message.pastLabel')}` : ''}</span>
        </summary>
        <ToolResultContent content={displayContent} isJson={isJson} />
      </details>
    )
  }

  if (message.type === 'error') {
    const errorText = message.content || message.message || t('message.errorOccurred')
    const isResolved = lastUserMsgIndex !== undefined && lastUserMsgIndex > message.index
    return (
      <div className={`message error-message${isResolved ? ' error-message--resolved' : ''}`}>
        <div className="bubble error">
          {isResolved && <span className="error-resolved-badge">{t('message.past')}</span>}
          <MarkdownRenderer>{errorText}</MarkdownRenderer>
        </div>
      </div>
    )
  }

  if (message.type === 'result') {
    const dur = message.duration_ms
    const turns = message.num_turns
    const usage = message.usage
    if (!dur && !usage) return null
    const durStr = dur != null ? `${(dur / 1000).toFixed(1)}s` : '?'
    const turnsStr = turns != null ? String(turns) : '?'
    const tokenStr = usage?.input_tokens != null && usage?.output_tokens != null
      ? `${((usage.input_tokens + usage.output_tokens) / 1000).toFixed(1)}K`
      : '?'
    return (
      <div className={`message result-footer${message.is_error ? ' result-footer--error' : ''}`}>
        <span className="result-footer__text">
          {t('message.resultUsage', { duration: durStr, turns: turnsStr, tokens: tokenStr })}
        </span>
      </div>
    )
  }

  if (message.type === 'file_upload') {
    const files = ((message.data as Array<{ filename: string; size?: number }> | undefined) || [])
      .filter(f => isValidFilename(f.filename))
    return (
      <div className="message system-message">
        <FileCardList files={files.map(f => ({ ...f, downloadUrl: undefined }))} status="uploaded" />
      </div>
    )
  }

  if (message.type === 'file_result') {
    const files = ((message.data as Array<{ filename: string; size?: number; download_url?: string }> | undefined) || [])
      .filter(f => isValidFilename(f.filename))
    const userId = message.user_id
    return (
      <>
        {files.map((f, i) => (
          <div key={i} className="message generated-file-message">
            <FileCardList
              files={[{
                ...f,
                downloadUrl: f.download_url
                  ? f.download_url
                  : (userId && message.session_id
                  ? (f.filename.startsWith('outputs/') || f.filename.startsWith('outputs\\')
                    ? `/api/users/${userId}/download/${f.filename.replace(/\\/g, '/')}`
                    : `/api/users/${userId}/download/outputs/${f.filename}`)
                  : undefined),
              }]}
              authToken={authToken}
              status="result"
              onFileClick={onFileClick}
            />
          </div>
        ))}
      </>
    )
  }

  // stream_event — compact activity indicator so user can see agent progress
  if (message.type === 'stream_event') {
    const event = message.event as Record<string, unknown> | undefined
    if (!event) return null

    const eventType = event.type as string | undefined
    const toolName = event.tool_name as string | undefined
    const progressMsg = event.message as string | undefined

    // content_block_delta — streaming text output
    // NOTE: Aggregation and display handled by App.tsx useStreamingText hook
    // MessageBubble should NOT render individual deltas to avoid duplicate display
    if (eventType === 'content_block_delta') {
      return null
    }

    if (eventType === 'tool_use' && toolName) {
      const icon = getToolIcon(toolName)
      return (
        <div className="message stream-event">
          <span className="stream-event__icon">{icon}</span>
          <span className="stream-event__text">{toolName}</span>
        </div>
      )
    }

    if (eventType === 'progress' && progressMsg) {
      return (
        <div className="message stream-event stream-event--progress">
          <span className="stream-event__text">{progressMsg}</span>
        </div>
      )
    }

    // Unknown event type — hide
    return null
  }

  // auth_error — handled in useWebSocket for reconnect, not displayed
  if (message.type === 'auth_error') {
    return null
  }

  // heartbeat — invisible in message list; used by ChatArea for staleness detection
  if (message.type === 'heartbeat') {
    return null
  }

  // assistant
  const hasContent = message.content && message.content.trim().length > 0
  if (!hasContent) {
    return null
  }

  const blocks = parseAssistantContent(message.content)
  const hasThinking = blocks.some(b => b.kind === 'thinking')
  const hasAnalysis = blocks.some(b => b.kind === 'analysis')
  const hasSummary = blocks.some(b => b.kind === 'summary')

  const textContent = blocks
    .filter(b => b.kind === 'text')
    .map(p => p.content)
    .join('\n')

  return (
    <div className="message assistant-message">
      <div className="bubble">
        {hasThinking && (
          <details className="thinking-block" open={false}>
            <summary>{t('message.thinking')}</summary>
            <div className="thinking-content">
              {blocks.filter(b => b.kind === 'thinking').map((p, i) => (
                <div key={i} className="thinking-text">{p.content}</div>
              ))}
            </div>
          </details>
        )}
        {hasAnalysis && (
          <details className="analysis-block" open={false}>
            <summary>{t('message.analysis')}</summary>
            <div className="analysis-content">
              {blocks.filter(b => b.kind === 'analysis').map((p, i) => (
                <div key={i} className="analysis-text">
                  <MarkdownRenderer>{p.content}</MarkdownRenderer>
                </div>
              ))}
            </div>
          </details>
        )}
        {hasSummary && (
          <details className="summary-block" open={false}>
            <summary>{t('message.summary')}</summary>
            <div className="summary-content">
              {blocks.filter(b => b.kind === 'summary').map((p, i) => (
                <div key={i} className="summary-text">
                  <MarkdownRenderer>{p.content}</MarkdownRenderer>
                </div>
              ))}
            </div>
          </details>
        )}
        {textContent && (
          <MarkdownRenderer>{textContent}</MarkdownRenderer>
        )}
      </div>
    </div>
  )
}

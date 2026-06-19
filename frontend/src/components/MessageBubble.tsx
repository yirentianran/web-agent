import { memo } from 'react'
import { useTranslation } from 'react-i18next'
import type { Message } from '../lib/types'
import MarkdownRenderer from './MarkdownRenderer'
import ErrorCard from './ErrorCard'
import { FileCardList } from './FileCards'
import AskUserQuestionCard from './AskUserQuestionCard'
import TodoWriteViz from './TodoWriteViz'
import { parseTodoWriteInput } from '../lib/todos'
import ToolCard, { ToolResultContent, getToolIcon, buildToolSummary, fmtDuration, detectLanguage, formatBashCommand, formatFileContent, formatEditContent, isValidFilename } from './ToolCard'


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
    .filter(msg => !(msg.type === 'tool_result' && msg.tool_use_id && resultMap.has(msg.tool_use_id)) && !(msg.type === 'tool_result' && msg.name === 'TodoWrite'))
    .map(msg => {
      if (msg.type === 'tool_use' && msg.id && msg.name !== 'AskUserQuestion' && msg.name !== 'TodoWrite') {
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

// ── Unified block parser ──────────────────────────────────────────

export type TagBlock = { kind: 'analysis' | 'summary' | 'text'; content: string }
type BlockKind = 'thinking' | 'analysis' | 'summary' | 'text'
interface BlockPart { kind: BlockKind; content: string }

/** Parse [thinking], <analysis>, and <summary> blocks from text. */
function parseBlocks(text: string): BlockPart[] {
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

/** Split text into analysis/summary/text blocks. Used by ChatArea streaming. */
export function parseTagBlocks(text: string): TagBlock[] {
  return parseBlocks(text)
    .filter(b => b.kind !== 'thinking')
    .map(b => ({ kind: b.kind as 'analysis' | 'summary' | 'text', content: b.content }))
}

/** Check if text has unclosed <analysis> or <summary> tags. */
export function hasIncompleteTag(text: string): boolean {
  const openAnalysis = (text.match(/<analysis>/g) || []).length
  const closeAnalysis = (text.match(/<\/analysis>/g) || []).length
  const openSummary = (text.match(/<summary>/g) || []).length
  const closeSummary = (text.match(/<\/summary>/g) || []).length
  return openAnalysis !== closeAnalysis || openSummary !== closeSummary
}

// ── Shared helpers ────────────────────────────────────────────────

function isResolvedMessage(message: Message, lastUserMsgIndex?: number): boolean {
  return lastUserMsgIndex !== undefined && lastUserMsgIndex > message.index
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

// ── Shared collapsible block (thinking / analysis / summary) ──────

interface CollapsibleBlockProps {
  kind: 'thinking' | 'analysis' | 'summary'
  items: Array<{ content: string }>
}

export function CollapsibleBlock({ kind, items }: CollapsibleBlockProps) {
  const { t } = useTranslation()
  if (items.length === 0) return null
  const labelKey = kind === 'thinking' ? 'message.thinking' : kind === 'analysis' ? 'message.analysis' : 'message.summary'
  const useMarkdown = kind !== 'thinking'
  return (
    <details className={`${kind}-block`} open={false}>
      <summary>{t(labelKey)}</summary>
      <div className={`${kind}-content`}>
        {items.map((item, i) => (
          <div key={i} className={`${kind}-text`}>
            {useMarkdown ? <MarkdownRenderer>{item.content}</MarkdownRenderer> : item.content}
          </div>
        ))}
      </div>
    </details>
  )
}

const MessageBubble = memo(function MessageBubble({ message, sessionId, onAnswer, onFileClick, onResend, lastTodoWriteIndex, lastUserMsgIndex, authToken }: MessageBubbleProps) {
  const { t, i18n } = useTranslation()
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
    return null
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
      return (
        <ToolCard name="Bash" summary={summary} toolResult={message.toolResult}>
          {description && <div className="tool-description">{description}</div>}
          <ToolResultContent content={command} language="bash" />
        </ToolCard>
      )
    }

    // Write tool_use: show file path + content
    if (message.name === 'Write' && input) {
      const { content, filePath } = formatFileContent(input)
      return (
        <ToolCard name="Write" summary={summary} toolResult={message.toolResult}>
          {filePath && <div className="tool-description">{filePath}</div>}
          <ToolResultContent content={content} language={detectLanguage(filePath)} />
        </ToolCard>
      )
    }

    // Edit tool_use: show old_string → new_string diff
    if (message.name === 'Edit' && input) {
      const { filePath, oldContent, newContent } = formatEditContent(input)
      const lang = detectLanguage(filePath)
      return (
        <ToolCard name="Edit" summary={summary} toolResult={message.toolResult}>
          {filePath && <div className="tool-description">{filePath}</div>}
          <div className="tool-edit-content">
            <div className="tool-edit-old">
              <span className="tool-edit-label">{t('message.removed')}</span>
              <ToolResultContent content={oldContent || t('message.none')} language={lang} />
            </div>
            <div className="tool-edit-new">
              <span className="tool-edit-label">{t('message.added')}</span>
              <ToolResultContent content={newContent || t('message.none')} language={lang} />
            </div>
          </div>
        </ToolCard>
      )
    }

    return (
      <ToolCard name={message.name || 'unknown'} summary={summary} toolResult={message.toolResult}>
        <ToolResultContent content={JSON.stringify(message.input, null, 2)} language="json" />
      </ToolCard>
    )
  }

  if (message.type === 'tool_result') {
    const rawContent = message.content || ''
    // Hide empty tool results (e.g., TaskOutput with no content) unless it's an error
    const isEmpty = !rawContent && !message.is_error
    const displayContent = rawContent || (message.is_error ? t('message.toolErrorNoOutput') : (isEmpty ? t('message.resultEmpty') : ''))
    const isResolved = message.is_error && isResolvedMessage(message, lastUserMsgIndex)

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
        <ToolResultContent content={displayContent} isBashResult={message.name === 'Bash'} />
      </details>
    )
  }

  if (message.type === 'error') {
    const errorText = message.message || message.content || t('message.errorOccurred')
    const isResolved = isResolvedMessage(message, lastUserMsgIndex)
    const severity = message.severity || 'retryable'
    return (
      <ErrorCard
        message={errorText}
        severity={severity}
        detail={message.detail}
        actions={message.actions}
        isResolved={isResolved}
        onAction={(kind) => {
          if (kind === 'new_session') {
            window.location.hash = ''
          }
        }}
      />
    )
  }

  if (message.type === 'result') {
    const dur = message.duration_ms
    const turns = message.num_turns
    const usage = message.usage
    if (!dur && !usage) return null
    const durStr = dur != null ? fmtDuration(dur, i18n.language) : '?'
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

  const blocks = parseBlocks(message.content)
  const thinkingItems = blocks.filter(b => b.kind === 'thinking')
  const analysisItems = blocks.filter(b => b.kind === 'analysis')
  const summaryItems = blocks.filter(b => b.kind === 'summary')
  const textContent = blocks
    .filter(b => b.kind === 'text')
    .map(p => p.content)
    .join('\n')

  return (
    <div className="message assistant-message">
      <div className="bubble">
        <CollapsibleBlock kind="thinking" items={thinkingItems} />
        <CollapsibleBlock kind="analysis" items={analysisItems} />
        <CollapsibleBlock kind="summary" items={summaryItems} />
        {textContent && (
          <MarkdownRenderer>{textContent}</MarkdownRenderer>
        )}
      </div>
    </div>
  )
});

export default MessageBubble;

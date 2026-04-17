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
  if (name === 'WebSearch') {
    return String(input.query || '')
  }
  if (name === 'WebFetch') {
    return String(input.url || '')
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

export function formatEditContent(input: Record<string, unknown>): FormattedEditContent {
  const filePath = input.file_path ? String(input.file_path) : null
  const oldStr = String(input.old_string ?? '')
  const newStr = String(input.new_string ?? '')
  return {
    filePath,
    oldContent: oldStr.replace(/\\r\\n/g, '\r\n').replace(/\\n/g, '\n'),
    newContent: newStr.replace(/\\r\\n/g, '\r\n').replace(/\\n/g, '\n'),
  }
}

// ── Thinking block parser ────────────────────────────────────────

function parseThinkingBlocks(text: string): Array<{ kind: 'thinking' | 'text'; content: string }> {
  const parts: Array<{ kind: 'thinking' | 'text'; content: string }> = []
  const matches = [...text.matchAll(/\[thinking\]([\s\S]*?)\[\/thinking\]/g)]

  if (matches.length === 0) {
    return [{ kind: 'text', content: text }]
  }

  let lastIndex = 0
  for (const match of matches) {
    if (match.index! > lastIndex) {
      parts.push({ kind: 'text', content: text.slice(lastIndex, match.index!) })
    }
    parts.push({ kind: 'thinking', content: match[1].trim() })
    lastIndex = match.index! + match[0].length
  }
  if (lastIndex < text.length) {
    parts.push({ kind: 'text', content: text.slice(lastIndex) })
  }
  return parts
}

interface MessageBubbleProps {
  message: Message
  sessionId: string
  onAnswer: (sessionId: string, answers: Record<string, string>) => void
  onFileClick?: (filename: string) => void
}

export default function MessageBubble({ message, sessionId, onAnswer, onFileClick }: MessageBubbleProps) {
  if (message.type === 'user') {
    const files = (message.data as Array<{ filename: string; size?: number }> | undefined) || []
    if ((!message.content || !message.content.trim()) && files.length === 0) return null
    const handleFileClick = onFileClick
      ? (filename: string) => { onFileClick(filename) }
      : undefined
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
              <MarkdownRenderer>{message.content}</MarkdownRenderer>
            </div>
          </div>
        )}
      </>
    )
  }

  if (message.type === 'system') {
    // Filter out internal system messages that shouldn't be displayed
    // - hook_started/response: shown as spinners in ChatArea
    // - init: internal initialization confirmation
    // - session_state_changed: used to update UI state, not displayed
    // - task_started / task_started.*: internal SDK task notifications
    const hiddenSubtypes = ['hook_started', 'hook_response', 'hook_error', 'init', 'session_state_changed', 'task_started']
    const subtype = message.subtype || ''
    if (hiddenSubtypes.includes(subtype) || subtype.startsWith('task_started.')) {
      return null
    }
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
        <details className="message tool-message" open={false}>
          <summary className="tool-summary">
            <span className="tool-icon">{getToolIcon(message.name)}</span>
            <span className="tool-name">{message.name}</span>
            {summary && <span className="tool-detail">{summary}</span>}
          </summary>
          {description && <div className="tool-description">{description}</div>}
          <pre className="tool-input"><code>{command}</code></pre>
        </details>
      )
    }

    // Write tool_use: show file path + content
    if (message.name === 'Write' && input) {
      const { content, filePath } = formatFileContent(input)
      return (
        <details className="message tool-message" open={false}>
          <summary className="tool-summary">
            <span className="tool-icon">{getToolIcon(message.name)}</span>
            <span className="tool-name">{message.name}</span>
            {summary && <span className="tool-detail">{summary}</span>}
          </summary>
          {filePath && <div className="tool-description">{filePath}</div>}
          <pre className="tool-input"><code>{content}</code></pre>
        </details>
      )
    }

    // Edit tool_use: show old_string → new_string diff
    if (message.name === 'Edit' && input) {
      const { filePath, oldContent, newContent } = formatEditContent(input)
      return (
        <details className="message tool-message" open={false}>
          <summary className="tool-summary">
            <span className="tool-icon">{getToolIcon(message.name)}</span>
            <span className="tool-name">{message.name}</span>
            {filePath && <span className="tool-detail">{filePath}</span>}
          </summary>
          {filePath && <div className="tool-description">{filePath}</div>}
          <div className="tool-edit-content">
            <div className="tool-edit-old">
              <span className="tool-edit-label">Removed:</span>
              <pre><code>{oldContent || '(none)'}</code></pre>
            </div>
            <div className="tool-edit-new">
              <span className="tool-edit-label">Added:</span>
              <pre><code>{newContent || '(none)'}</code></pre>
            </div>
          </div>
        </details>
      )
    }

    return (
      <details className="message tool-message" open={false}>
        <summary className="tool-summary">
          <span className="tool-icon">{getToolIcon(message.name)}</span>
          <span className="tool-name">{message.name || 'unknown'}</span>
          {summary && <span className="tool-detail">{summary}</span>}
        </summary>
        <pre className="tool-input">{JSON.stringify(message.input, null, 2)}</pre>
      </details>
    )
  }

  if (message.type === 'tool_result') {
    const content = message.content || ''
    // Hide empty tool results (e.g., TaskOutput with no content)
    if (!content && !message.is_error) return null
    const isJson = /^\s*[{[]/.test(content)
    return (
      <details className="message tool-result">
        <summary>Result: {message.name || 'unknown'}</summary>
        {isJson ? (
          <pre className="tool-output tool-output-json"><code>{content}</code></pre>
        ) : (
          <div className="tool-output-markdown">
            <MarkdownRenderer>{content}</MarkdownRenderer>
          </div>
        )}
      </details>
    )
  }

  if (message.type === 'error') {
    return (
      <div className="message error-message">
        <div className="bubble error">{message.content || 'An error occurred'}</div>
      </div>
    )
  }

  if (message.type === 'result') {
    return null
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
                downloadUrl: f.download_url ?? (userId && message.session_id
                  ? `/api/users/${userId}/download/outputs/${f.filename}`
                  : undefined),
              }]}
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

  // heartbeat — invisible in message list; used by ChatArea for staleness detection
  if (message.type === 'heartbeat') {
    return null
  }

  // assistant
  const hasContent = message.content && message.content.trim().length > 0
  if (!hasContent) return null

  const parts = parseThinkingBlocks(message.content)
  const hasThinking = parts.some(p => p.kind === 'thinking')

  return (
    <div className="message assistant-message">
      <div className="bubble">
        {hasThinking && (
          <details className="thinking-block" open={false}>
            <summary>Thinking</summary>
            <div className="thinking-content">
              {parts.filter(p => p.kind === 'thinking').map((p, i) => (
                <div key={i} className="thinking-text">{p.content}</div>
              ))}
            </div>
          </details>
        )}
        <MarkdownRenderer>
          {parts.filter(p => p.kind === 'text').map(p => p.content).join('\n')}
        </MarkdownRenderer>
      </div>
    </div>
  )
}

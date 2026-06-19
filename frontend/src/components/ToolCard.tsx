import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Message } from '../lib/types'
import MarkdownRenderer from './MarkdownRenderer'
import { useCopyToClipboard } from '../hooks/useCopyToClipboard'

// ── Shared constants ─────────────────────────────────────────────

const MAX_CONTENT_HEIGHT = 300

// ── Filename validation ──────────────────────────────────────────

const INVALID_FILENAMES = new Set(['null', 'undefined'])

export function isValidFilename(name: string | undefined | null): boolean {
  if (!name) return false
  if (INVALID_FILENAMES.has(name.toLowerCase())) return false
  return true
}

// ── Duration formatting ──────────────────────────────────────────

const UNITS_ZH = { sec: '秒', min: '分', hr: '时' }
const UNITS_EN = { sec: 's', min: 'm', hr: 'h' }

export function fmtDuration(ms: number, lang: string): string {
  const u = lang === 'zh' ? UNITS_ZH : UNITS_EN
  const totalSec = ms / 1000
  if (totalSec < 60) return `${(Math.round(totalSec * 10) / 10)}${u.sec}`
  const totalMin = Math.floor(totalSec / 60)
  const sec = Math.floor(totalSec % 60)
  if (totalMin < 60) return `${totalMin}${u.min} ${sec}${u.sec}`
  const hr = Math.floor(totalMin / 60)
  const min = totalMin % 60
  return `${hr}${u.hr} ${min}${u.min}`
}

// ── Tool display helpers ─────────────────────────────────────────

const DISABLED_TOOLS = ['WebSearch', 'WebFetch'] as const

export const TOOL_ICONS: Record<string, string> = {
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

export function getToolIcon(name?: string): string {
  return TOOL_ICONS[name || ''] || '🔧'
}

export function buildToolSummary(name: string | undefined, input: Record<string, unknown>): string {
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

// ── Copy button (used by ToolResultContent) ──────────────────────

function CopyButton({ content }: { content: string }) {
  const { t } = useTranslation()
  const { copied, copy } = useCopyToClipboard()

  return (
    <button className="tool-output-copy-btn" onClick={() => copy(content)}>
      {copied ? t('message.resultCopied') : t('message.copyResult')}
    </button>
  )
}

// ── File extension → highlight language ──────────────────────────

const LANG_MAP: Record<string, string> = {
  ts: 'typescript', tsx: 'tsx', js: 'javascript', jsx: 'jsx',
  py: 'python', rs: 'rust', go: 'go', java: 'java', rb: 'ruby',
  php: 'php', css: 'css', scss: 'scss', html: 'html', htm: 'html',
  json: 'json', yaml: 'yaml', yml: 'yaml', toml: 'toml', xml: 'xml',
  sql: 'sql', sh: 'bash', bash: 'bash', zsh: 'bash',
  md: 'markdown', c: 'c', cpp: 'cpp', h: 'c', hpp: 'cpp',
  vue: 'html', svelte: 'html', svg: 'xml',
}

export function detectLanguage(filePath?: string | null): string | undefined {
  if (!filePath) return undefined
  const ext = filePath.split('.').pop()?.toLowerCase()
  if (!ext) return undefined
  return LANG_MAP[ext]
}

// ── Collapsible content wrapper ──────────────────────────────────

interface CollapsibleContentProps {
  containerRef: React.RefObject<HTMLDivElement | null>
  isLarge: boolean
  expanded: boolean
  onToggle: () => void
  expandLabel: string
  collapseLabel: string
  children: React.ReactNode
}

function CollapsibleContent({ containerRef, isLarge, expanded, onToggle, expandLabel, collapseLabel, children }: CollapsibleContentProps) {
  return (
    <>
      <div
        ref={containerRef}
        className={`tool-output-markdown${isLarge && !expanded ? ' tool-output-collapsed' : ''}`}
      >
        {children}
      </div>
      {isLarge && (
        <button className="tool-output-expand-btn" onClick={onToggle}>
          {expanded ? collapseLabel : expandLabel}
        </button>
      )}
    </>
  )
}

// ── Tool content renderer (input + output) ───────────────────────

export function ToolResultContent({ content, isBashResult, language }: { content: string; isBashResult?: boolean; language?: string }) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const [isLarge, setIsLarge] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  const isInput = language !== undefined
  const isMarkdown = !isInput && /^(#{1,6}\s|\*[\*\*]|__|\s*[-*+]\s|\s*\d+\.\s|\[.+?\]\(.+?\)|\s*>\s|\s*\|)/m.test(content.trim())
  const isHtml = !isInput && /<(!DOCTYPE|[a-z]+\b[^>]*\/?>)/i.test(content.trim())

  useEffect(() => {
    setExpanded(false)
    if (!containerRef.current) return
    if (content.length < 200 && !isLarge) return
    setIsLarge(prev => {
      const val = containerRef.current!.scrollHeight > MAX_CONTENT_HEIGHT
      return prev !== val ? val : prev
    })
  }, [content])

  if (isHtml) {
    return (
      <div className="tool-output-wrapper">
        <div className="tool-output-header">
          <CopyButton content={content} />
        </div>
        <div className="tool-output-markdown">
          <MarkdownRenderer allowHtml>{content}</MarkdownRenderer>
        </div>
      </div>
    )
  }

  if (isMarkdown) {
    return (
      <div className="tool-output-wrapper">
        <div className="tool-output-header">
          <CopyButton content={content} />
        </div>
        <CollapsibleContent
          containerRef={containerRef}
          isLarge={isLarge}
          expanded={expanded}
          onToggle={() => setExpanded(e => !e)}
          expandLabel={t('message.showAll')}
          collapseLabel={t('message.collapse')}
        >
          <MarkdownRenderer>{content}</MarkdownRenderer>
        </CollapsibleContent>
      </div>
    )
  }

  const isJson = !isInput && /^\s*[{[]/.test(content.trim())

  let displayContent: string
  if (isInput) {
    displayContent = '```' + (language || '') + '\n' + content + '\n```'
  } else if (isJson) {
    if (expanded) {
      try {
        displayContent = '```json\n' + JSON.stringify(JSON.parse(content), null, 2) + '\n```'
      } catch {
        displayContent = '```text\n' + content + '\n```'
      }
    } else {
      displayContent = '```json\n' + content + '\n```'
    }
  } else if (isBashResult) {
    displayContent = '```bash\n' + content + '\n```'
  } else {
    displayContent = '```text\n' + content + '\n```'
  }

  return (
    <div className="tool-output-wrapper">
      <CollapsibleContent
        containerRef={containerRef}
        isLarge={isLarge}
        expanded={expanded}
        onToggle={() => setExpanded(e => !e)}
        expandLabel={t('message.showAll')}
        collapseLabel={t('message.collapse')}
      >
        <MarkdownRenderer>{displayContent}</MarkdownRenderer>
      </CollapsibleContent>
    </div>
  )
}

// ── Tool result section ──────────────────────────────────────────

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
        <ToolResultContent content={content} isBashResult={toolResult.name === 'Bash'} />
      )}
    </div>
  )
}

// ── Tool card component ──────────────────────────────────────────

export interface ToolCardProps {
  name: string
  summary: string
  toolResult?: Message['toolResult']
  children: React.ReactNode
}

function ToolCard({ name, summary, toolResult, children }: ToolCardProps) {
  const isDanger = DANGER_TOOLS.has(name)
  return (
    <details
      className={`message tool-message${isDanger ? ' tool-message--danger' : ''}`}
      open={false}
    >
      <summary className="tool-summary">
        <span className="tool-icon">{getToolIcon(name)}</span>
        <span className="tool-name">{name}</span>
        {summary && <span className="tool-detail">{summary}</span>}
      </summary>
      {children}
      <ToolResultSection toolResult={toolResult} />
    </details>
  )
}

export default ToolCard

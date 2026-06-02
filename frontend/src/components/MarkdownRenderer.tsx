import { useState, useCallback, useMemo, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'

interface MarkdownRendererProps {
  children: string
}

/** Upgrade outer fences to 4 backticks when nested code blocks are detected. */
function fixNestedCodeBlocks(text: string): string {
  const lines = text.split('\n')
  const result: string[] = [...lines]
  let modified = false

  for (let i = 0; i < lines.length; i++) {
    const openMatch = lines[i].match(/^(`{3})(\S+)/)
    if (!openMatch) continue

    const outerLen = openMatch[1].length
    const lang = openMatch[2]

    // Collect subsequent bare ``` fences (potential inner fences + outer closing).
    // Stop at another ```<lang> opening after at least one bare ``` — likely a separate block.
    const bareFences: number[] = []
    for (let j = i + 1; j < lines.length; j++) {
      if (lines[j] === '```') {
        bareFences.push(j)
      } else if (lines[j].match(/^`{3}\S+/) && bareFences.length > 0) {
        break
      }
    }

    if (bareFences.length < 2) continue

    const hasYaml = i + 1 < lines.length && lines[i + 1].trim() === '---'
    let hasHeadings = false
    for (let j = i + 1; j < bareFences[0]; j++) {
      if (lines[j].match(/^#{1,6}\s/)) {
        hasHeadings = true
        break
      }
    }

    if (!hasYaml && !(lang === 'markdown' && hasHeadings)) continue

    const trueClose = bareFences[bareFences.length - 1]
    const newLen = outerLen + 1
    result[i] = '`'.repeat(newLen) + lang
    result[trueClose] = '`'.repeat(newLen)
    modified = true
  }

  return modified ? result.join('\n') : text
}

function copyToClipboard(text: string): Promise<void> {
  return navigator.clipboard.writeText(text)
}

/** Extract plain text from React children (may be string, number, or element tree). */
function extractText(children: ReactNode): string {
  if (typeof children === 'string') return children
  if (typeof children === 'number') return String(children)
  if (Array.isArray(children)) return children.map(extractText).join('')
  if (children && typeof children === 'object' && 'props' in (children as object)) {
    return extractText((children as { props: { children?: ReactNode } }).props.children)
  }
  return ''
}

function CodeBlock({ className, children, node: _node, ...props }: React.ComponentProps<'code'> & { node?: unknown }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  const match = /language-(\w+)/.exec(className || '')
  const language = match ? match[1] : ''
  const isBlock = !!match

  const handleCopy = useCallback(() => {
    const text = extractText(children)
    copyToClipboard(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }).catch(() => {})
  }, [children])

  if (!isBlock) {
    return (
      <code className={className} {...props}>
        {children}
      </code>
    )
  }

  return (
    <div className="code-block">
      <div className="code-block-header">
        <span className="code-block-lang">{language}</span>
        <button
          className="code-block-copy"
          onClick={handleCopy}
          aria-label={t('markdown.copyCode')}
          type="button"
        >
          {copied ? t('markdown.copied') : t('markdown.copy')}
        </button>
      </div>
      <pre className="code-block-pre">
        <code className={className} {...props}>
          {children}
        </code>
      </pre>
    </div>
  )
}

function PreBlock({ children, node: _node }: React.ComponentProps<'pre'> & { node?: unknown }) {
  // Unwrap ReactMarkdown's <pre> wrapper; CodeBlock already renders its own container.
  return <>{children}</>
}

export default function MarkdownRenderer({ children }: MarkdownRendererProps) {
  const processed = useMemo(() => fixNestedCodeBlocks(children), [children])
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeHighlight]}
      components={{
        code: CodeBlock as any,
        pre: PreBlock as any,
      }}
    >
      {processed}
    </ReactMarkdown>
  )
}

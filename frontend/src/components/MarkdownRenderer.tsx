import { useState, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'

interface MarkdownRendererProps {
  children: string
}

function copyToClipboard(text: string): Promise<void> {
  return navigator.clipboard.writeText(text)
}

function CodeBlock({ className, children, ...props }: React.ComponentProps<'code'>) {
  const [copied, setCopied] = useState(false)
  const match = /language-(\w+)/.exec(className || '')
  const language = match ? match[1] : ''
  const isBlock = !!match

  const handleCopy = useCallback(() => {
    const text = typeof children === 'string' ? children : ''
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
          aria-label="Copy code"
          type="button"
        >
          {copied ? 'Copied!' : 'Copy'}
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

export default function MarkdownRenderer({ children }: MarkdownRendererProps) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeHighlight]}
      components={{
        code: CodeBlock as any,
      }}
    >
      {children}
    </ReactMarkdown>
  )
}

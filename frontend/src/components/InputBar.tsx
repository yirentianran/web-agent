import { useState, useRef, useImperativeHandle, forwardRef, useEffect, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

type UploadStatus = 'pending' | 'uploading' | 'uploaded' | 'failed'

interface AttachedFile {
  file: File
  status: UploadStatus
  id: string
}

interface InputBarProps {
  onSend: (message: string, fileMeta?: Array<{filename: string; size: number}>) => void
  onEnsureSession: () => Promise<string | undefined>
  onStop?: () => void
  disabled?: boolean
  isRunning?: boolean
  userId?: string
  authToken?: string
  sessionId?: string
}

export interface InputBarHandle {
  insertText: (text: string) => void
}

let fileCounter = 0

const InputBar = forwardRef<InputBarHandle, InputBarProps>(
  function InputBar({ onSend, onEnsureSession, onStop, disabled, isRunning, userId, authToken, sessionId }: InputBarProps, ref) {
    const { t } = useTranslation()
    const [input, setInput] = useState('')
    const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([])
    const fileInputRef = useRef<HTMLInputElement>(null)
    const textareaRef = useRef<HTMLTextAreaElement>(null)

    // Per-session draft preservation — save input/files when switching away,
    // restore when switching back. Avoids losing typed content on session switch.
    const inputRef = useRef(input)
    inputRef.current = input
    const filesRef = useRef(attachedFiles)
    filesRef.current = attachedFiles
    const draftsRef = useRef<Map<string, { input: string; files: AttachedFile[] }>>(new Map())
    const prevSessionRef = useRef(sessionId)

    useEffect(() => {
      const prevId = prevSessionRef.current
      const newId = sessionId

      if (prevId && prevId !== newId) {
        draftsRef.current.set(prevId, {
          input: inputRef.current,
          files: filesRef.current,
        })
      }

      if (newId) {
        const draft = draftsRef.current.get(newId)
        setInput(draft?.input ?? '')
        setAttachedFiles(draft?.files ?? [])
      } else {
        setInput('')
        setAttachedFiles([])
      }

      prevSessionRef.current = newId
    }, [sessionId])

    // Expose insertText method for click-to-reference
    useImperativeHandle(ref, () => ({
      insertText: (text: string) => {
        setInput(prev => {
          const cursorPos = textareaRef.current?.selectionStart ?? prev.length
          const before = prev.slice(0, cursorPos)
          const after = prev.slice(cursorPos)
          const newText = before + text + after
          return newText
        })
        textareaRef.current?.focus()
      },
    }))

    const autoResize = () => {
      const ta = textareaRef.current
      if (ta) {
        ta.style.height = 'auto'
        ta.style.height = Math.min(ta.scrollHeight, 120) + 'px'
      }
    }

    // Auto-resize textarea when input or files change
    useEffect(() => {
      autoResize()
    }, [input, attachedFiles])

    // ── Upload helpers ────────────────────────────────────────

    const uploadFile = async (af: AttachedFile): Promise<{ success: boolean; filename?: string; size?: number }> => {
      if (!userId) return { success: false }
      const formData = new FormData()
      formData.append('file', af.file)
      if (sessionId) formData.append('session_id', sessionId)
      try {
        const headers: Record<string, string> = {}
        if (authToken) headers["Authorization"] = `Bearer ${authToken}`
        const resp = await fetch(`/api/users/${userId}/upload`, { method: 'POST', headers, body: formData })
        if (resp.ok) {
          const data = await resp.json()
          if (data.filename) {
            setAttachedFiles(prev => prev.map(f =>
              f.id === af.id ? { ...f } : f
            ))
            return { success: true, filename: data.filename, size: data.size ?? af.file.size }
          }
        }
        return { success: resp.ok }
      } catch {
        return { success: false }
      }
    }

    // ── Form submission ──────────────────────────────────────

    const handleSubmit = async (e: FormEvent) => {
      e.preventDefault()
      const trimmed = input.trim()
      const hasUploading = attachedFiles.some(f => f.status === 'uploading')
      if ((!trimmed && attachedFiles.length === 0) || disabled || hasUploading) return

      // All files are already uploaded (or there are none) — send immediately
      const uploadedFiles = attachedFiles.filter(f => f.status === 'uploaded')
      const fileMeta = uploadedFiles.map(f => ({ filename: f.file.name, size: f.file.size }))
      const refFiles = fileMeta.map(f => f.filename)
      let messageContent = trimmed
      if (refFiles.length > 0 && trimmed) {
        const refs = refFiles.map(name => `@${name}`).join(' ')
        messageContent = `${refs} ${trimmed}`
      }
      onSend(messageContent, fileMeta.length > 0 ? fileMeta : undefined)
      setInput('')
      setAttachedFiles([])
      if (fileInputRef.current) fileInputRef.current.value = ''
    }

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
        e.preventDefault()
        handleSubmit(e)
      }
    }

    // ── File selection (upload immediately) ───────────────────

    const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files || [])
      if (files.length === 0) return

      const newFiles: AttachedFile[] = files.map(f => ({
        file: f,
        status: 'pending' as const,
        id: `file-${++fileCounter}`,
      }))
      setAttachedFiles(prev => [...prev, ...newFiles])
      if (fileInputRef.current) fileInputRef.current.value = ''

      // Ensure session exists before uploading
      let uploadSessionId = sessionId
      if (!uploadSessionId) {
        uploadSessionId = await onEnsureSession()
        if (!uploadSessionId) return
      }
      await uploadFiles(newFiles, uploadSessionId)
    }

    const uploadFiles = async (files: AttachedFile[], uploadSessionId: string) => {
      // Mark all as uploading
      setAttachedFiles(prev => prev.map(f => {
        const match = files.find(nf => nf.id === f.id)
        return match ? { ...f, status: 'uploading' as const } : f
      }))

      for (const af of files) {
        const formData = new FormData()
        formData.append('file', af.file)
        formData.append('session_id', uploadSessionId)
        try {
          const headers: Record<string, string> = {}
          if (authToken) headers['Authorization'] = `Bearer ${authToken}`
          const resp = await fetch(`/api/users/${userId}/upload`, { method: 'POST', headers, body: formData })
          if (resp.ok) {
            setAttachedFiles(prev => prev.map(f =>
              f.id === af.id ? { ...f, status: 'uploaded' as const } : f,
            ))
          } else {
            setAttachedFiles(prev => prev.map(f =>
              f.id === af.id ? { ...f, status: 'failed' as const } : f,
            ))
          }
        } catch {
          setAttachedFiles(prev => prev.map(f =>
            f.id === af.id ? { ...f, status: 'failed' as const } : f,
          ))
        }
      }
    }

    // ── File actions ─────────────────────────────────────────

    const removeFile = (index: number) => {
      setAttachedFiles(prev => prev.filter((_, i) => i !== index))
    }

    const retryFile = async (index: number) => {
      const af = attachedFiles[index]
      if (!af) return

      setAttachedFiles(prev => prev.map((f, i) => i === index ? { ...f, status: 'uploading' as const } : f))
      const result = await uploadFile(af)
      setAttachedFiles(prev => prev.map((f, i) =>
        i === index ? { ...f, status: result.success ? 'uploaded' as const : 'failed' as const } : f,
      ))
    }

    // ── Derived state ────────────────────────────────────────

    const isUploading = attachedFiles.some(f => f.status === 'uploading')
    const hasFailed = attachedFiles.some(f => f.status === 'failed')
    const blockedByUpload = isUploading || hasFailed

    return (
      <form className="input-bar" onSubmit={handleSubmit}>
        {/* Attach Button - Circular */}
        <button
          type="button"
          className="btn-attach"
          onClick={() => fileInputRef.current?.click()}
          disabled={disabled || isUploading}
          aria-label={t('input.attachFile')}
        >
          &#128206;
        </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          style={{ display: 'none' }}
          onChange={handleFileChange}
        />

        {/* Input Box */}
        <div className="input-box">
          {/* Attached Files Inside Input */}
          {attachedFiles.length > 0 && (
            <div className="input-attached-files">
              {attachedFiles.map((f, i) => (
                <span key={f.id} className={`input-file-chip input-file-chip--${f.status}`}>
                  {f.status === 'uploading' && <span className="file-chip-spinner" />}
                  {f.status === 'uploaded' && <span className="file-chip-status status-ok">&#10003;</span>}
                  {f.status === 'failed' && <span className="file-chip-status status-err">&#10007;</span>}
                  {f.file.name}
                  {f.status === 'failed' && (
                    <span className="file-chip-actions">
                      <button
                        type="button"
                        className="file-chip-btn file-chip-btn--retry"
                        onClick={() => retryFile(i)}
                        aria-label={t('input.retryUpload', { filename: f.file.name })}
                      >
                        &#8635;
                      </button>
                      <button
                        type="button"
                        className="file-chip-btn file-chip-btn--remove"
                        onClick={() => removeFile(i)}
                        aria-label={t('input.removeFile', { filename: f.file.name })}
                      >
                        &times;
                      </button>
                    </span>
                  )}
                  {f.status === 'pending' && (
                    <button
                      type="button"
                      className="file-chip-btn file-chip-btn--remove"
                      onClick={() => removeFile(i)}
                      aria-label={t('input.removeFile', { filename: f.file.name })}
                    >
                      &times;
                    </button>
                  )}
                  {f.status === 'uploaded' && (
                    <button
                      type="button"
                      className="file-chip-btn file-chip-btn--remove"
                      onClick={() => removeFile(i)}
                      aria-label={t('input.removeFile', { filename: f.file.name })}
                    >
                      &times;
                    </button>
                  )}
                  {f.status === 'uploading' && (
                    <span className="file-chip-actions">
                      <span className="file-chip-spinner-label">{t('input.uploading')}</span>
                    </span>
                  )}
                </span>
              ))}
              {isUploading && <span className="input-upload-status">{t('input.uploadingFiles')}</span>}
            </div>
          )}
          {/* Input Field */}
          <textarea
            ref={textareaRef}
            className="input-field"
            value={input}
            onChange={(e) => { setInput(e.target.value); autoResize() }}
            onKeyDown={handleKeyDown}
            placeholder={t('input.placeholder')}
            disabled={disabled}
            rows={3}
          />
        </div>

        {/* Send/Stop Button - Circular */}
        {isRunning && onStop ? (
          <button
            type="button"
            className="btn-stop"
            onClick={onStop}
            aria-label={t('input.stop')}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          </button>
        ) : (
          <button
            type="submit"
            className="btn-send"
            aria-label={t('input.send')}
            disabled={disabled || (!input.trim() && attachedFiles.length === 0) || blockedByUpload}
            title={hasFailed ? t('input.uploadFailedTooltip') : isUploading ? t('input.uploadingTooltip') : undefined}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M5 12h14M12 5l7 7-7 7" />
            </svg>
          </button>
        )}
      </form>
    )
  },
)

export default InputBar
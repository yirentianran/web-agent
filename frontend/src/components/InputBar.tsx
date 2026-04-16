import { useState, useRef, useImperativeHandle, forwardRef, type FormEvent } from 'react'

type UploadStatus = 'pending' | 'uploading' | 'uploaded' | 'failed'

interface AttachedFile {
  file: File
  status: UploadStatus
  id: string
}

interface InputBarProps {
  onSend: (message: string, files?: File[]) => void
  onStop?: () => void
  disabled?: boolean
  userId?: string
}

export interface InputBarHandle {
  insertText: (text: string) => void
}

let fileCounter = 0

const InputBar = forwardRef<InputBarHandle, InputBarProps>(
  function InputBar({ onSend, onStop, disabled, userId }: InputBarProps, ref) {
    const [input, setInput] = useState('')
    const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([])
    const fileInputRef = useRef<HTMLInputElement>(null)
    const textareaRef = useRef<HTMLTextAreaElement>(null)

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

    // ── Upload helpers ────────────────────────────────────────

    const uploadFile = async (af: AttachedFile): Promise<boolean> => {
      if (!userId) return false
      const formData = new FormData()
      formData.append('file', af.file)
      try {
        const resp = await fetch(`/api/users/${userId}/upload`, { method: 'POST', body: formData })
        return resp.ok
      } catch {
        return false
      }
    }

    const uploadAllPending = async (): Promise<{ uploadedFiles: File[]; allSuccess: boolean }> => {
      const pending = attachedFiles.filter(f => f.status === 'pending' || f.status === 'failed')
      if (pending.length === 0) {
        return { uploadedFiles: attachedFiles.map(f => f.file), allSuccess: true }
      }

      // Mark all as uploading
      setAttachedFiles(prev => prev.map(f =>
        (f.status === 'pending' || f.status === 'failed') ? { ...f, status: 'uploading' as const } : f,
      ))

      let allSuccess = true
      const results: Array<{ id: string; success: boolean }> = []

      for (const af of pending) {
        const success = await uploadFile(af)
        results.push({ id: af.id, success })
        if (!success) allSuccess = false
      }

      // Update statuses based on results
      setAttachedFiles(prev => prev.map(f => {
        const result = results.find(r => r.id === f.id)
        if (result) {
          return { ...f, status: result.success ? 'uploaded' as const : 'failed' as const }
        }
        return f
      }))

      return {
        uploadedFiles: attachedFiles.filter(f => {
          const result = results.find(r => r.id === f.id)
          return result?.success ?? f.status === 'uploaded'
        }).map(f => f.file),
        allSuccess,
      }
    }

    // ── Form submission ──────────────────────────────────────

    const handleSubmit = async (e: FormEvent) => {
      e.preventDefault()
      const trimmed = input.trim()
      const hasPending = attachedFiles.some(f => f.status === 'pending' || f.status === 'failed')
      const hasUploading = attachedFiles.some(f => f.status === 'uploading')
      if ((!trimmed && attachedFiles.length === 0) || disabled || hasUploading) return

      // If there are pending or failed files, upload them first
      if (hasPending) {
        const { uploadedFiles, allSuccess } = await uploadAllPending()
        if (!allSuccess) {
          // Upload had failures — don't send, let user retry or remove
          return
        }
        // Send message with successfully uploaded files
        let messageContent = trimmed
        if (uploadedFiles.length > 0 && trimmed) {
          const refs = uploadedFiles.map(f => `@${f.name}`).join(' ')
          messageContent = `${refs} ${trimmed}`
        }
        onSend(messageContent, uploadedFiles.length > 0 ? uploadedFiles : undefined)
        setInput('')
        setAttachedFiles([])
        if (fileInputRef.current) fileInputRef.current.value = ''
        return
      }

      // All files already uploaded
      const uploadedFiles = attachedFiles.filter(f => f.status === 'uploaded').map(f => f.file)
      let messageContent = trimmed
      if (uploadedFiles.length > 0 && trimmed) {
        const refs = uploadedFiles.map(f => `@${f.name}`).join(' ')
        messageContent = `${refs} ${trimmed}`
      }
      onSend(messageContent, uploadedFiles.length > 0 ? uploadedFiles : undefined)
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

    // ── File selection (store as pending, don't upload yet) ──

    const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files || [])
      if (files.length === 0) return

      const newFiles: AttachedFile[] = files.map(f => ({
        file: f,
        status: 'pending' as const,
        id: `file-${++fileCounter}`,
      }))
      setAttachedFiles(prev => [...prev, ...newFiles])
      if (fileInputRef.current) fileInputRef.current.value = ''
    }

    // ── File actions ─────────────────────────────────────────

    const removeFile = (index: number) => {
      setAttachedFiles(prev => prev.filter((_, i) => i !== index))
    }

    const retryFile = async (index: number) => {
      const af = attachedFiles[index]
      if (!af) return

      setAttachedFiles(prev => prev.map((f, i) => i === index ? { ...f, status: 'uploading' as const } : f))
      const success = await uploadFile(af)
      setAttachedFiles(prev => prev.map((f, i) =>
        i === index ? { ...f, status: success ? 'uploaded' as const : 'failed' as const } : f,
      ))
    }

    const autoResize = () => {
      const ta = textareaRef.current
      if (ta) {
        ta.style.height = 'auto'
        ta.style.height = Math.min(ta.scrollHeight, 120) + 'px'
      }
    }

    // ── Derived state ────────────────────────────────────────

    const isUploading = attachedFiles.some(f => f.status === 'uploading')
    const hasFailed = attachedFiles.some(f => f.status === 'failed')
    // Pending files are OK — clicking send will trigger upload.
    // Only block when uploads are in progress or there are unresolved failures.
    const blockedByUpload = isUploading || hasFailed

    return (
      <form className="input-bar" onSubmit={handleSubmit}>
        {/* Attach Button - Circular */}
        <button
          type="button"
          className="btn-attach"
          onClick={() => fileInputRef.current?.click()}
          disabled={disabled || isUploading}
          aria-label="Attach file"
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
                        aria-label={`Retry upload for ${f.file.name}`}
                      >
                        &#8635;
                      </button>
                      <button
                        type="button"
                        className="file-chip-btn file-chip-btn--remove"
                        onClick={() => removeFile(i)}
                        aria-label={`Remove ${f.file.name}`}
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
                      aria-label={`Remove ${f.file.name}`}
                    >
                      &times;
                    </button>
                  )}
                  {f.status === 'uploaded' && (
                    <button
                      type="button"
                      className="file-chip-btn file-chip-btn--remove"
                      onClick={() => removeFile(i)}
                      aria-label={`Remove ${f.file.name}`}
                    >
                      &times;
                    </button>
                  )}
                  {f.status === 'uploading' && (
                    <span className="file-chip-actions">
                      <span className="file-chip-spinner-label">Uploading...</span>
                    </span>
                  )}
                </span>
              ))}
              {isUploading && <span className="input-upload-status">Uploading files...</span>}
            </div>
          )}
          {/* Input Field */}
          <textarea
            ref={textareaRef}
            className="input-field"
            value={input}
            onChange={(e) => { setInput(e.target.value); autoResize() }}
            onKeyDown={handleKeyDown}
            placeholder="Enter instruction... (Shift+Enter for newline)"
            disabled={disabled}
            rows={3}
          />
        </div>

        {/* Send/Stop Button - Circular */}
        {disabled && onStop ? (
          <button
            type="button"
            className="btn-stop"
            onClick={onStop}
            aria-label="Stop session"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          </button>
        ) : (
          <button
            type="submit"
            className="btn-send"
            aria-label="Send message"
            disabled={disabled || (!input.trim() && attachedFiles.length === 0) || blockedByUpload}
            title={hasFailed ? 'Upload failed — retry or remove files' : isUploading ? 'Uploading files...' : undefined}
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

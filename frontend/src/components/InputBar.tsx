import { useState, useRef, useImperativeHandle, forwardRef, useEffect, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

type UploadStatus = 'pending' | 'uploading' | 'uploaded' | 'failed'

interface AttachedFile {
  file: File
  status: UploadStatus
  id: string
  storedName?: string
  storedSize?: number
}

interface InputBarProps {
  onSend: (message: string, files?: File[], fileMeta?: Array<{stored_name: string; size: number}>) => void
  onStop?: () => void
  disabled?: boolean
  isRunning?: boolean
  userId?: string
  authToken?: string
}

export interface InputBarHandle {
  insertText: (text: string) => void
}

let fileCounter = 0

const InputBar = forwardRef<InputBarHandle, InputBarProps>(
  function InputBar({ onSend, onStop, disabled, isRunning, userId, authToken }: InputBarProps, ref) {
    const { t } = useTranslation()
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

    const autoResize = () => {
      const ta = textareaRef.current
      if (ta) {
        ta.style.height = 'auto'
        ta.style.height = Math.min(ta.scrollHeight, 120) + 'px'
      }
    }

    // Reset textarea height after input is cleared (form submit)
    // Call autoResize — it recalculates height based on current content
    useEffect(() => {
      if (input === '' && attachedFiles.length === 0) {
        autoResize()
      }
    }, [input, attachedFiles])

    // ── Upload helpers ────────────────────────────────────────

    const uploadFile = async (af: AttachedFile): Promise<{ success: boolean; storedName?: string; storedSize?: number }> => {
      if (!userId) return { success: false }
      const formData = new FormData()
      formData.append('file', af.file)
      try {
        const headers: Record<string, string> = {}
        if (authToken) headers["Authorization"] = `Bearer ${authToken}`
        const resp = await fetch(`/api/users/${userId}/upload`, { method: 'POST', headers, body: formData })
        if (resp.ok) {
          const data = await resp.json()
          if (data.stored_name) {
            setAttachedFiles(prev => prev.map(f =>
              f.id === af.id ? { ...f, storedName: data.stored_name, storedSize: data.size ?? af.file.size } : f
            ))
            return { success: true, storedName: data.stored_name, storedSize: data.size ?? af.file.size }
          }
        }
        return { success: resp.ok }
      } catch {
        return { success: false }
      }
    }

    const uploadAllPending = async (): Promise<{ fileMeta: Array<{stored_name: string; filename: string; size: number}>; fileObjs: File[]; allSuccess: boolean }> => {
      const pending = attachedFiles.filter(f => f.status === 'pending' || f.status === 'failed')
      if (pending.length === 0) {
        return {
          fileMeta: attachedFiles.filter(f => f.storedName).map(f => ({ stored_name: f.storedName!, filename: f.file.name, size: f.storedSize ?? f.file.size })),
          fileObjs: attachedFiles.map(f => f.file),
          allSuccess: true,
        }
      }

      // Mark all as uploading
      setAttachedFiles(prev => prev.map(f =>
        (f.status === 'pending' || f.status === 'failed') ? { ...f, status: 'uploading' as const } : f,
      ))

      let allSuccess = true
      const results: Array<{ id: string; success: boolean; storedName?: string; storedSize?: number }> = []

      for (const af of pending) {
        const result = await uploadFile(af)
        results.push({ id: af.id, ...result })
        if (!result.success) allSuccess = false
      }

      // Update statuses based on results
      setAttachedFiles(prev => prev.map(f => {
        const result = results.find(r => r.id === f.id)
        if (result) {
          return { ...f, status: result.success ? 'uploaded' as const : 'failed' as const }
        }
        return f
      }))

      // Build metadata directly from upload responses, not from async React state
      const fileMeta = results
        .filter(r => r.success && r.storedName)
        .map(r => ({
          stored_name: r.storedName!,
          filename: pending.find(f => f.id === r.id)?.file.name ?? r.storedName!,
          size: r.storedSize ?? 0,
        }))
      const fileObjs = results
        .filter(r => r.success)
        .map(r => pending.find(f => f.id === r.id)!)
        .filter(Boolean)
        .map(f => f.file)

      return { fileMeta, fileObjs, allSuccess }
    }

    // ── Form submission ──────────────────────────────────────

    const handleSubmit = async (e: FormEvent) => {
      e.preventDefault()
      const trimmed = input.trim()
      const hasPending = attachedFiles.some(f => f.status === 'pending' || f.status === 'failed')
      const hasUploading = attachedFiles.some(f => f.status === 'uploading')
      if ((!trimmed && attachedFiles.length === 0) || disabled || hasUploading) return

      // Collect stored names for already-uploaded files
      const uploadedWithStored = attachedFiles.filter(f => f.status === 'uploaded' && f.storedName)

      // If there are pending or failed files, upload them first
      if (hasPending) {
        const { allSuccess, fileMeta, fileObjs } = await uploadAllPending()
        if (!allSuccess) {
          // Upload had failures — don't send, let user retry or remove
          return
        }
        const refFiles = fileMeta.length > 0 ? fileMeta.map(f => f.filename) : fileObjs.map(f => f.name)
        let messageContent = trimmed
        if (refFiles.length > 0 && trimmed) {
          const refs = refFiles.map(name => `@${name}`).join(' ')
          messageContent = `${refs} ${trimmed}`
        }
        onSend(messageContent, fileObjs, fileMeta.length > 0 ? fileMeta : undefined)
        setInput('')
        setAttachedFiles([])
        if (fileInputRef.current) fileInputRef.current.value = ''
        return
      }

      // All files already uploaded
      const fileMeta = uploadedWithStored
        .map(f => ({ stored_name: f.storedName!, filename: f.file.name, size: f.storedSize ?? f.file.size }))
        .filter(f => f.stored_name)
      const uploadedFileObjects = attachedFiles.map(f => f.file)
      const refFiles = fileMeta.length > 0 ? fileMeta.map(f => f.filename) : uploadedFileObjects.map(f => f.name)
      let messageContent = trimmed
      if (refFiles.length > 0 && trimmed) {
        const refs = refFiles.map(name => `@${name}`).join(' ')
        messageContent = `${refs} ${trimmed}`
      }
      onSend(messageContent, uploadedFileObjects, fileMeta.length > 0 ? fileMeta : undefined)
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

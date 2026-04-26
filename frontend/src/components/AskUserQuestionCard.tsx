import { useState, useRef } from 'react'
import type { AskUserQuestionInput } from '../lib/types'

interface AskUserQuestionCardProps {
  input: AskUserQuestionInput
  sessionId: string
  onAnswer: (sessionId: string, answers: Record<string, string>) => void
}

function isAskUserInput(input: unknown): input is AskUserQuestionInput {
  if (!input || typeof input !== 'object') return false
  const obj = input as Record<string, unknown>
  return Array.isArray(obj.questions) && obj.questions.length > 0
}

const OTHER_OPTION = '__other__'

export default function AskUserQuestionCard({ input, sessionId, onAnswer }: AskUserQuestionCardProps) {
  const [selected, setSelected] = useState<Record<number, string>>({})
  const [customInputs, setCustomInputs] = useState<Record<number, string>>({})
  const [submitted, setSubmitted] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  // Ref for synchronous guard — prevents double-submit race condition
  const submittedRef = useRef(false)

  if (!isAskUserInput(input)) {
    return (
      <div className="question-card question-error">
        <p>Unable to parse question payload.</p>
        <pre>{JSON.stringify(input, null, 2)}</pre>
      </div>
    )
  }

  const isAnswered = (i: number): boolean => {
    const sel = selected[i]
    if (!sel) return false
    if (sel === OTHER_OPTION) {
      return (customInputs[i] || '').trim().length > 0
    }
    return true
  }

  const canSubmit = input.questions.every((_q, i) => isAnswered(i))

  const handleSubmit = () => {
    // Synchronous guard prevents double-submission (ref is instant, state is async)
    if (!canSubmit || submittedRef.current) return
    submittedRef.current = true

    const answers: Record<string, string> = {}
    input.questions.forEach((q, i) => {
      const sel = selected[i]
      if (sel === OTHER_OPTION) {
        answers[q.question] = customInputs[i]?.trim()!
      } else {
        answers[q.question] = sel
      }
    })

    try {
      onAnswer(sessionId, answers)
      setSubmitted(true)
    } catch (e) {
      // If onAnswer throws synchronously, allow retry
      submittedRef.current = false
      setSubmitError(e instanceof Error ? e.message : 'Failed to submit answer')
    }
  }

  if (submitted) {
    return (
      <div className="question-card question-submitted">
        <div className="question-submitted-icon">&#10003;</div>
        <p className="question-submitted-text">Answer submitted</p>
      </div>
    )
  }

  const totalQuestions = input.questions.length
  const answeredCount = input.questions.filter((_q, i) => isAnswered(i)).length

  return (
    <div className="question-card">
      {input.questions.map((q, i) => {
        const isOtherSelected = selected[i] === OTHER_OPTION
        return (
          <div key={i} className="question-block">
            {q.header && <span className="question-header">{q.header}</span>}
            <p className="question-text">{q.question}</p>
            <div className="options-list">
              {q.options.map((opt) => {
                const isSelected = selected[i] === opt.label
                return (
                  <button
                    key={opt.label}
                    className={`option-btn ${isSelected ? 'selected' : ''}`}
                    onClick={() => {
                      setSelected((prev) => ({ ...prev, [i]: opt.label }))
                      setSubmitError(null)
                    }}
                    type="button"
                  >
                    <span className="option-label">{opt.label}</span>
                    {opt.description && (
                      <span className="option-desc">{opt.description}</span>
                    )}
                  </button>
                )
              })}
              <button
                className={`option-btn option-other ${isOtherSelected ? 'selected' : ''}`}
                onClick={() => {
                  setSelected((prev) => ({ ...prev, [i]: OTHER_OPTION }))
                  setSubmitError(null)
                }}
                type="button"
              >
                <span className="option-label">Other / Custom...</span>
                <span className="option-desc">None of the above — specify your own</span>
              </button>
            </div>
            {isOtherSelected && (
              <textarea
                className="custom-answer-input"
                placeholder="Describe your answer..."
                value={customInputs[i] || ''}
                onChange={(e) => {
                  setCustomInputs((prev) => ({ ...prev, [i]: e.target.value }))
                  setSubmitError(null)
                }}
                rows={2}
              />
            )}
          </div>
        )
      })}
      {submitError && (
        <div className="question-submit-error">{submitError}</div>
      )}
      <button
        className="btn-submit-answer"
        disabled={!canSubmit}
        onClick={handleSubmit}
        type="button"
      >
        {canSubmit ? 'Submit' : `Select all ${totalQuestions} options (${answeredCount}/${totalQuestions})`}
      </button>
    </div>
  )
}

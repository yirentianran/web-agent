import { useState } from 'react'
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

export default function AskUserQuestionCard({ input, sessionId, onAnswer }: AskUserQuestionCardProps) {
  const [selected, setSelected] = useState<Record<number, string>>({})

  if (!isAskUserInput(input)) {
    return (
      <div className="question-card question-error">
        <p>Unable to parse question payload.</p>
        <pre>{JSON.stringify(input, null, 2)}</pre>
      </div>
    )
  }

  const canSubmit = input.questions.length === Object.keys(selected).length

  const handleSubmit = () => {
    if (!canSubmit) return
    const answers: Record<string, string> = {}
    input.questions.forEach((q, i) => {
      answers[q.question] = selected[i]
    })
    onAnswer(sessionId, answers)
  }

  return (
    <div className="question-card">
      {input.questions.map((q, i) => (
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
                  onClick={() => setSelected((prev) => ({ ...prev, [i]: opt.label }))}
                  type="button"
                >
                  <span className="option-label">{opt.label}</span>
                  {opt.description && (
                    <span className="option-desc">{opt.description}</span>
                  )}
                </button>
              )
            })}
          </div>
        </div>
      ))}
      <button
        className="btn-submit-answer"
        disabled={!canSubmit}
        onClick={handleSubmit}
        type="button"
      >
        {canSubmit ? 'Submit' : `Select all ${input.questions.length} options`}
      </button>
    </div>
  )
}

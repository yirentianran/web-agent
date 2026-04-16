import { useState } from 'react'

interface SkillFeedbackWidgetProps {
  skillName?: string
  onSubmit: (rating: number, comment: string) => Promise<void>
}

export default function SkillFeedbackWidget({ skillName, onSubmit }: SkillFeedbackWidgetProps) {
  const [rating, setRating] = useState(0)
  const [comment, setComment] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [loading, setLoading] = useState(false)
  const [collapsed, setCollapsed] = useState(true)

  const handleSubmit = async () => {
    if (rating === 0) return
    setLoading(true)
    try {
      await onSubmit(rating, comment)
      setSubmitted(true)
    } catch {
      // Keep UI visible so user can retry
    } finally {
      setLoading(false)
    }
  }

  if (submitted) {
    return (
      <div className="feedback-widget feedback-submitted">
        <p>Thank you for your feedback!</p>
      </div>
    )
  }

  return (
    <div className={`feedback-widget ${collapsed ? 'collapsed' : ''}`}>
      {collapsed ? (
        <button
          className="feedback-trigger"
          onClick={() => setCollapsed(false)}
          type="button"
          aria-label="Show feedback widget"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
          Feedback
        </button>
      ) : (
        <>
          <div className="feedback-header">
            <span className="feedback-label">
              {skillName ? `Rate ${skillName}` : 'Rate this result'}
            </span>
            <button
              className="feedback-close"
              onClick={() => setCollapsed(true)}
              type="button"
              aria-label="Hide feedback widget"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
            </button>
          </div>
          <div className="feedback-stars">
            {[1, 2, 3, 4, 5].map((star) => (
              <button
                key={star}
                className={`star ${star <= rating ? 'filled' : ''}`}
                onClick={() => setRating(star)}
                type="button"
                aria-label={`${star} star${star > 1 ? 's' : ''}`}
              >
                {star <= rating ? '\u2605' : '\u2606'}
              </button>
            ))}
          </div>
          <textarea
            className="feedback-comment"
            placeholder="What could be improved? (optional)"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            rows={2}
          />
          <button
            className="btn-submit-feedback"
            disabled={rating === 0 || loading}
            onClick={handleSubmit}
            type="button"
          >
            {loading ? 'Submitting...' : 'Submit Feedback'}
          </button>
        </>
      )}
    </div>
  )
}

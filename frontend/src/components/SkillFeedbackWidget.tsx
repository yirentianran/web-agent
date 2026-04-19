import { useState } from 'react'

interface SkillFeedbackWidgetProps {
  skillNames?: string[]
  onSubmit: (rating: number, comment: string, userEdits: string, skillName: string) => Promise<void>
}

export default function SkillFeedbackWidget({ skillNames, onSubmit }: SkillFeedbackWidgetProps) {
  const [rating, setRating] = useState(0)
  const [comment, setComment] = useState('')
  const [userEdits, setUserEdits] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [loading, setLoading] = useState(false)
  const [collapsed, setCollapsed] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showEdits, setShowEdits] = useState(false)

  // Derive the effective skill name for display and submission.
  // When multiple skills exist, the user selects which one to rate.
  const [selectedSkill, setSelectedSkill] = useState(
    skillNames && skillNames.length === 1 ? skillNames[0] : skillNames?.[0] ?? '',
  )

  // Sync selectedSkill when skillNames prop changes (e.g. messages update)
  const effectiveSkillNames = skillNames && skillNames.length > 0 ? skillNames : null
  const displaySkillName = effectiveSkillNames && effectiveSkillNames.length === 1
    ? effectiveSkillNames[0]
    : selectedSkill

  const handleSubmit = async () => {
    if (rating === 0) return
    if (!selectedSkill && effectiveSkillNames) return
    setLoading(true)
    setError(null)
    try {
      const skillName = effectiveSkillNames && effectiveSkillNames.length === 1
        ? effectiveSkillNames[0]
        : selectedSkill || 'general'
      await onSubmit(rating, comment, userEdits, skillName)
      setSubmitted(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit feedback. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  // Reset error when user changes input
  const handleRatingChange = (star: number) => {
    setRating(star)
    if (error) setError(null)
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
              {displaySkillName ? `Rate ${displaySkillName}` : 'Rate this result'}
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
          {effectiveSkillNames && effectiveSkillNames.length > 1 && (
            <select
              className="feedback-skill-select"
              value={selectedSkill}
              onChange={(e) => setSelectedSkill(e.target.value)}
              aria-label="Select skill to rate"
            >
              {effectiveSkillNames.map((name) => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
          )}
          <div className="feedback-stars">
            {[1, 2, 3, 4, 5].map((star) => (
              <button
                key={star}
                className={`star ${star <= rating ? 'filled' : ''}`}
                onClick={() => handleRatingChange(star)}
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
            onChange={(e) => { setComment(e.target.value); if (error) setError(null) }}
            rows={2}
          />
          <details className="feedback-edits-toggle">
            <summary onClick={(e) => { e.preventDefault(); setShowEdits(!showEdits) }}>
              What did you change? (optional)
            </summary>
            <textarea
              className="feedback-comment"
              placeholder="Describe any edits you made..."
              value={userEdits}
              onChange={(e) => setUserEdits(e.target.value)}
              rows={2}
            />
          </details>
          {error && (
            <div className="feedback-error">
              <span>{error}</span>
              <button onClick={handleSubmit} type="button" disabled={loading}>
                {loading ? 'Retrying...' : 'Retry'}
              </button>
            </div>
          )}
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

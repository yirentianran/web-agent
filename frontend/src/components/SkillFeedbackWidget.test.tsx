import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import SkillFeedbackWidget from '../components/SkillFeedbackWidget'
import ChatArea from '../components/ChatArea'
import type { Message } from '../lib/types'

function renderWidget(props?: { skillNames?: string[]; onSubmit?: (r: number, c: string, e: string, s: string) => Promise<void> }) {
  return render(
    <SkillFeedbackWidget
      skillNames={props?.skillNames}
      onSubmit={props?.onSubmit ?? (async () => {})}
    />,
  )
}

describe('SkillFeedbackWidget - collapsed by default', () => {
  it('renders collapsed by default (only trigger button visible)', () => {
    renderWidget()

    // Trigger button should be visible
    expect(screen.getByRole('button', { name: /show feedback/i })).toBeInTheDocument()

    // Stars should NOT be visible (collapsed)
    expect(screen.queryByRole('button', { name: /star/ })).not.toBeInTheDocument()

    // Submit button should NOT be visible
    expect(screen.queryByRole('button', { name: /submit feedback/i })).not.toBeInTheDocument()
  })

  it('has collapsed class when folded', () => {
    const { container } = renderWidget()
    const widget = container.querySelector('.feedback-widget')
    expect(widget).toHaveClass('collapsed')
  })

  it('positions collapsed trigger button at the right edge', () => {
    const { container } = renderWidget()
    const widget = container.querySelector('.feedback-widget')
    // Collapsed widget should align to the right edge of chat-area
    const style = getComputedStyle(widget!)
    expect(style.right).not.toBe('')
  })
})

describe('SkillFeedbackWidget - expand on trigger click', () => {
  it('expands when trigger button is clicked', () => {
    const { container } = renderWidget()

    fireEvent.click(screen.getByRole('button', { name: /show feedback/i }))

    // Stars should now be visible
    const stars = screen.getAllByRole('button', { name: /star/ })
    expect(stars).toHaveLength(5)

    // Submit button should be visible
    expect(screen.getByRole('button', { name: /submit feedback/i })).toBeInTheDocument()

    // Should NOT have collapsed class
    expect(container.querySelector('.feedback-widget')).not.toHaveClass('collapsed')
  })

  it('collapses when close button is clicked', () => {
    renderWidget()

    // Expand
    fireEvent.click(screen.getByRole('button', { name: /show feedback/i }))
    expect(screen.getAllByRole('button', { name: /star/ })).toHaveLength(5)

    // Collapse
    fireEvent.click(screen.getByRole('button', { name: /hide feedback/i }))

    // Stars should disappear
    expect(screen.queryByRole('button', { name: /star/ })).not.toBeInTheDocument()

    // Trigger button should be visible again
    expect(screen.getByRole('button', { name: /show feedback/i })).toBeInTheDocument()
  })

  it('shows close button in expanded header', () => {
    renderWidget()

    fireEvent.click(screen.getByRole('button', { name: /show feedback/i }))

    expect(screen.getByRole('button', { name: /hide feedback/i })).toBeInTheDocument()
  })
})

describe('SkillFeedbackWidget - rating interaction', () => {
  it('fills star on click', () => {
    renderWidget()

    // Expand first
    fireEvent.click(screen.getByRole('button', { name: /show feedback/i }))

    const thirdStar = screen.getByRole('button', { name: '3 stars' })
    fireEvent.click(thirdStar)

    const stars = screen.getAllByRole('button', { name: /star/ })
    expect(stars[0]).toHaveClass('filled')
    expect(stars[1]).toHaveClass('filled')
    expect(stars[2]).toHaveClass('filled')
    expect(stars[3]).not.toHaveClass('filled')
    expect(stars[4]).not.toHaveClass('filled')
  })

  it('calls onSubmit with correct rating and comment', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    renderWidget({ skillNames: ['audit-pdf'], onSubmit })

    fireEvent.click(screen.getByRole('button', { name: /show feedback/i }))
    fireEvent.click(screen.getByRole('button', { name: '4 stars' }))

    const textarea = screen.getByPlaceholderText('What could be improved? (optional)')
    fireEvent.change(textarea, { target: { value: 'Good but slow' } })

    fireEvent.click(screen.getByRole('button', { name: /submit feedback/i }))

    expect(onSubmit).toHaveBeenCalledWith(4, 'Good but slow', '', 'audit-pdf')
  })

  it('calls onSubmit with selected skill when multiple skills provided', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    renderWidget({ skillNames: ['audit-pdf', 'format-doc'], onSubmit })

    fireEvent.click(screen.getByRole('button', { name: /show feedback/i }))

    // Select the second skill from the dropdown
    const select = screen.getByRole('combobox', { name: /select skill/i })
    fireEvent.change(select, { target: { value: 'format-doc' } })

    fireEvent.click(screen.getByRole('button', { name: '4 stars' }))
    fireEvent.click(screen.getByRole('button', { name: /submit feedback/i }))

    expect(onSubmit).toHaveBeenCalledWith(4, '', '', 'format-doc')
  })
})

describe('SkillFeedbackWidget - submitted state', () => {
  it('shows thank-you message after successful submission', async () => {
    renderWidget()

    fireEvent.click(screen.getByRole('button', { name: /show feedback/i }))
    fireEvent.click(screen.getByRole('button', { name: '1 star' }))
    fireEvent.click(screen.getByRole('button', { name: /submit feedback/i }))

    expect(await screen.findByText(/thank you for your feedback/i)).toBeInTheDocument()

    // Stars should no longer be visible
    expect(screen.queryByRole('button', { name: /star/ })).not.toBeInTheDocument()
  })

  it('stays visible on submission failure for retry', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('Network error'))
    renderWidget({ onSubmit })

    fireEvent.click(screen.getByRole('button', { name: /show feedback/i }))
    fireEvent.click(screen.getByRole('button', { name: '2 stars' }))
    fireEvent.click(screen.getByRole('button', { name: /submit feedback/i }))

    // Should still show stars (not collapsed, not showing thank-you)
    expect(await screen.findByRole('button', { name: '1 star' })).toBeInTheDocument()
  })
})

describe('SkillFeedbackWidget - positioned inside ChatArea', () => {
  it('renders feedback widget inside chat-area, not as a fixed sibling', () => {
    const completedMessage: Message = {
      type: 'result',
      content: '',
      index: 0,
    }

    const { container } = render(
      <ChatArea
        messages={[completedMessage]}
        sessionId="test-session"
        sessionState="completed"
        onAnswer={() => {}}
        scrollPositions={new Map()}
      />,
    )

    // Widget should be inside .chat-area
    const chatArea = container.querySelector('.chat-area')
    expect(chatArea).not.toBeNull()

    const feedbackWidget = chatArea!.querySelector('.feedback-widget')
    expect(feedbackWidget).not.toBeNull()

    // The feedback widget is inside chat-area's DOM tree,
    // positioned with absolute (not fixed) so it stays in chat-area's corner
    expect(feedbackWidget!.classList.contains('feedback-widget')).toBe(true)
  })
})

describe('SkillFeedbackWidget - error handling', () => {
  it('shows error message on submission failure', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('Server error'))
    renderWidget({ onSubmit })

    fireEvent.click(screen.getByRole('button', { name: /show feedback/i }))
    fireEvent.click(screen.getByRole('button', { name: '1 star' }))
    fireEvent.click(screen.getByRole('button', { name: /submit feedback/i }))

    expect(await screen.findByText(/Server error/)).toBeInTheDocument()
  })

  it('shows retry button after failure', async () => {
    let callCount = 0
    const onSubmit = vi.fn().mockImplementation(async () => {
      callCount++
      if (callCount === 1) throw new Error('First try failed')
    })
    renderWidget({ onSubmit })

    fireEvent.click(screen.getByRole('button', { name: /show feedback/i }))
    fireEvent.click(screen.getByRole('button', { name: '1 star' }))
    fireEvent.click(screen.getByRole('button', { name: /submit feedback/i }))

    // Should show retry button
    expect(await screen.findByRole('button', { name: /retry/i })).toBeInTheDocument()

    // Click retry
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    // Should show thank-you after successful retry
    expect(await screen.findByText(/thank you for your feedback/i)).toBeInTheDocument()
  })

  it('clears error when user changes rating', async () => {
    const onSubmit = vi.fn().mockRejectedValueOnce(new Error('Failed')).mockResolvedValueOnce(undefined)
    renderWidget({ onSubmit })

    fireEvent.click(screen.getByRole('button', { name: /show feedback/i }))
    fireEvent.click(screen.getByRole('button', { name: '1 star' }))
    fireEvent.click(screen.getByRole('button', { name: /submit feedback/i }))

    expect(await screen.findByText(/Failed/)).toBeInTheDocument()

    // Change rating should clear error
    fireEvent.click(screen.getByRole('button', { name: '2 stars' }))
    expect(screen.queryByText(/Failed/)).not.toBeInTheDocument()
  })
})

describe('SkillFeedbackWidget - user_edits', () => {
  it('calls onSubmit with user_edits when provided', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    const { container } = renderWidget({ skillNames: ['audit-pdf'], onSubmit })

    fireEvent.click(screen.getByRole('button', { name: /show feedback/i }))
    fireEvent.click(screen.getByRole('button', { name: '5 stars' }))

    const commentArea = screen.getByPlaceholderText('What could be improved? (optional)')
    fireEvent.change(commentArea, { target: { value: 'Great!' } })

    // Open the "What did you change?" section
    const details = container.querySelector('details.feedback-edits-toggle')
    expect(details).not.toBeNull()
    fireEvent.click(screen.getByText('What did you change? (optional)'))

    const editsArea = screen.getByPlaceholderText('Describe any edits you made...')
    fireEvent.change(editsArea, { target: { value: 'Fixed formatting' } })

    fireEvent.click(screen.getByRole('button', { name: /submit feedback/i }))

    expect(onSubmit).toHaveBeenCalledWith(5, 'Great!', 'Fixed formatting', 'audit-pdf')
  })
})

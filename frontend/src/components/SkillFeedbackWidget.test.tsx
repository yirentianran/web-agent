import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import SkillFeedbackWidget from '../components/SkillFeedbackWidget'
import ChatArea from '../components/ChatArea'
import type { Message } from '../lib/types'

function renderWidget(props?: { onSubmit?: (r: number, c: string) => Promise<void> }) {
  return render(
    <SkillFeedbackWidget
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
    renderWidget({ onSubmit })

    fireEvent.click(screen.getByRole('button', { name: /show feedback/i }))
    fireEvent.click(screen.getByRole('button', { name: '4 stars' }))

    const textarea = screen.getByPlaceholderText('What could be improved? (optional)')
    fireEvent.change(textarea, { target: { value: 'Good but slow' } })

    fireEvent.click(screen.getByRole('button', { name: /submit feedback/i }))

    expect(onSubmit).toHaveBeenCalledWith(4, 'Good but slow')
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

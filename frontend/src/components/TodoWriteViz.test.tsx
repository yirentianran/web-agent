import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import TodoWriteViz from '../components/TodoWriteViz'
import type { TodoWriteTodo } from '../lib/types'

function renderViz(todos: TodoWriteTodo[]) {
  return render(<TodoWriteViz todos={todos} />)
}

describe('TodoWriteViz - rendering', () => {
  it('renders nothing when todos array is empty', () => {
    const { container } = renderViz([])
    expect(container.firstChild).toBeNull()
  })

  it('renders header with todo count', () => {
    renderViz([
      { content: 'Task one', status: 'pending' },
      { content: 'Task two', status: 'in_progress' },
    ])

    expect(screen.getByText(/2/)).toBeInTheDocument()
  })

  it('renders all todo items', () => {
    renderViz([
      { content: 'Check input files', status: 'completed' },
      { content: 'Extract PDF data', status: 'in_progress', activeForm: '正在编写PDF数据提取脚本' },
      { content: 'Fill Excel template', status: 'pending' },
    ])

    expect(screen.getByText('Check input files')).toBeInTheDocument()
    expect(screen.getByText('Extract PDF data')).toBeInTheDocument()
    expect(screen.getByText('Fill Excel template')).toBeInTheDocument()
  })
})

describe('TodoWriteViz - status icons', () => {
  it('shows checkmark for completed todos', () => {
    renderViz([{ content: 'Done task', status: 'completed' }])
    const item = screen.getByText('Done task').closest('.todoviz-item')
    expect(item).toHaveClass('completed')
  })

  it('shows activeForm text for in_progress todos', () => {
    renderViz([
      { content: 'Running task', status: 'in_progress', activeForm: '正在运行' },
    ])

    expect(screen.getByText('正在运行')).toBeInTheDocument()
  })

  it('shows circle for pending todos', () => {
    renderViz([{ content: 'Waiting task', status: 'pending' }])
    const item = screen.getByText('Waiting task').closest('.todoviz-item')
    expect(item).toHaveClass('pending')
  })
})

describe('TodoWriteViz - progress bar', () => {
  it('shows progress bar with correct fill percentage', () => {
    renderViz([
      { content: 'A', status: 'completed' },
      { content: 'B', status: 'completed' },
      { content: 'C', status: 'pending' },
      { content: 'D', status: 'in_progress' },
    ])

    // 2 completed out of 4 = 50%
    const progressBar = screen.getByRole('progressbar')
    expect(progressBar).toHaveAttribute('aria-valuenow', '50')
  })

  it('shows 100% progress when all todos are completed', () => {
    renderViz([
      { content: 'A', status: 'completed' },
      { content: 'B', status: 'completed' },
    ])

    const progressBar = screen.getByRole('progressbar')
    expect(progressBar).toHaveAttribute('aria-valuenow', '100')
  })

  it('shows 0% progress when no todos are completed', () => {
    renderViz([
      { content: 'A', status: 'pending' },
      { content: 'B', status: 'in_progress' },
    ])

    const progressBar = screen.getByRole('progressbar')
    expect(progressBar).toHaveAttribute('aria-valuenow', '0')
  })
})

describe('TodoWriteViz - strikethrough', () => {
  it('adds completed class for strikethrough on completed todo text', () => {
    renderViz([{ content: 'Finished task', status: 'completed' }])

    const textEl = screen.getByText('Finished task')
    expect(textEl).toHaveClass('completed')
  })

  it('does not add completed class for pending todos', () => {
    renderViz([
      { content: 'Pending task', status: 'pending' },
    ])

    const textEl = screen.getByText('Pending task')
    expect(textEl).not.toHaveClass('completed')
  })
})

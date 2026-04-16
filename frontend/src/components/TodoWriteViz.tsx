import { useMemo } from 'react'
import type { TodoWriteTodo } from '../lib/types'
import './TodoWriteViz.css'

interface TodoWriteVizProps {
  todos: TodoWriteTodo[]
}

const STATUS_ICONS: Record<TodoWriteTodo['status'], string> = {
  pending: '\u25CB',
  in_progress: '\u25CC',
  completed: '\u2713',
  deleted: '\u2717',
}

const STATUS_COLORS: Record<TodoWriteTodo['status'], string> = {
  pending: '#94a3b8',
  in_progress: '#3b82f6',
  completed: '#22c55e',
  deleted: '#ef4444',
}

export default function TodoWriteViz({ todos }: TodoWriteVizProps) {
  const stats = useMemo(() => {
    const completed = todos.filter(t => t.status === 'completed').length
    const total = todos.length
    const percentage = total > 0 ? Math.round((completed / total) * 100) : 0
    return { completed, total, percentage }
  }, [todos])

  if (todos.length === 0) return null

  return (
    <div className="todoviz">
      <div className="todoviz-header">
        <span className="todoviz-title">Tasks</span>
        <span className="todoviz-count">{stats.completed}/{stats.total}</span>
      </div>
      <div className="todoviz-progress-track" role="progressbar" aria-valuenow={stats.percentage} aria-valuemin={0} aria-valuemax={100}>
        <div className="todoviz-progress-fill" style={{ width: `${stats.percentage}%` }} />
      </div>
      <ul className="todoviz-list">
        {todos.map((todo, i) => (
          <li key={i} className={`todoviz-item ${todo.status}`}>
            <span className="todoviz-icon" style={{ color: STATUS_COLORS[todo.status] }}>
              {STATUS_ICONS[todo.status]}
            </span>
            <span className={`todoviz-text ${todo.status === 'completed' ? 'completed' : ''}`}>
              {todo.content}
            </span>
            {todo.status === 'in_progress' && todo.activeForm && (
              <span className="todoviz-active-form">
                {todo.activeForm}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

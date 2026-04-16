import type { TodoWriteTodo } from './types'

const VALID_STATUSES = new Set(['pending', 'in_progress', 'completed', 'deleted'])

export function parseTodoWriteInput(input: unknown): TodoWriteTodo[] | null {
  if (input == null) return null

  const record = input as Record<string, unknown>
  if (!Array.isArray(record.todos)) return null

  return record.todos
    .map((item: unknown) => {
      const obj = item as Record<string, unknown>
      const status = obj.status as string
      if (!VALID_STATUSES.has(status)) return null

      return {
        content: typeof obj.content === 'string' ? obj.content : '',
        status: status as TodoWriteTodo['status'],
        ...(obj.activeForm && typeof obj.activeForm === 'string'
          ? { activeForm: obj.activeForm }
          : {}),
      }
    })
    .filter((t): t is TodoWriteTodo => t !== null)
}

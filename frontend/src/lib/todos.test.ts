import { describe, it, expect } from 'vitest'
import { parseTodoWriteInput } from '../lib/todos'

describe('parseTodoWriteInput', () => {
  it('parses valid todo array with all fields', () => {
    const input = {
      todos: [
        { content: 'Task A', status: 'completed', activeForm: 'done' },
        { content: 'Task B', status: 'pending' },
      ],
    }

    const result = parseTodoWriteInput(input)

    expect(result).toHaveLength(2)
    expect(result![0]).toEqual({
      content: 'Task A',
      status: 'completed',
      activeForm: 'done',
    })
  })

  it('returns null when input is null', () => {
    expect(parseTodoWriteInput(null)).toBeNull()
  })

  it('returns null when input is undefined', () => {
    expect(parseTodoWriteInput(undefined)).toBeNull()
  })

  it('returns null when todos is not an array', () => {
    expect(parseTodoWriteInput({ todos: 'not-an-array' })).toBeNull()
  })

  it('returns empty array when todos array is empty', () => {
    const result = parseTodoWriteInput({ todos: [] })
    expect(result).toEqual([])
  })

  it('handles missing optional fields (activeForm)', () => {
    const input = {
      todos: [
        { content: 'No active form', status: 'pending' },
      ],
    }

    const result = parseTodoWriteInput(input)

    expect(result).toHaveLength(1)
    expect(result![0].activeForm).toBeUndefined()
  })

  it('skips todos with unknown status values', () => {
    const input = {
      todos: [
        { content: 'Valid', status: 'completed' },
        { content: 'Invalid', status: 'unknown_status' },
      ],
    }

    const result = parseTodoWriteInput(input)

    expect(result).toHaveLength(1)
    expect(result![0].content).toBe('Valid')
  })

  it('handles missing content field by using empty string', () => {
    const input = {
      todos: [
        { status: 'pending' },
      ],
    }

    const result = parseTodoWriteInput(input)

    expect(result).toHaveLength(1)
    expect(result![0].content).toBe('')
  })
})

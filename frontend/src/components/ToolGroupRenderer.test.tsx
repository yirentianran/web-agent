import { describe, it, expect } from 'vitest'
import { groupConsecutiveTools } from './ToolGroupRenderer'
import type { Message } from '../lib/types'

function makeTool(name: string, index: number, durationMs = 0): Message {
  return {
    type: 'tool_use',
    name,
    index,
    content: '',
    input: { file_path: `/path/${index}` },
    duration_ms: durationMs,
  } as Message
}

function makeUser(index: number): Message {
  return { type: 'user', index, content: 'hello' } as Message
}

function makeAssistant(index: number): Message {
  return { type: 'assistant', index, content: 'text' } as Message
}

describe('groupConsecutiveTools', () => {
  it('does not group fewer than 3 same-tool calls', () => {
    const msgs = [makeTool('Read', 1), makeTool('Read', 2), makeUser(3)]
    const result = groupConsecutiveTools(msgs)
    expect(result).toHaveLength(3)
    expect(result[0].type).toBe('tool_use')
    expect(result[1].type).toBe('tool_use')
  })

  it('groups 3 consecutive same-tool calls', () => {
    const msgs = [
      makeTool('Read', 1, 100),
      makeTool('Read', 2, 200),
      makeTool('Read', 3, 300),
      makeUser(4),
    ]
    const result = groupConsecutiveTools(msgs)
    expect(result).toHaveLength(2)
    const group = result[0]
    expect(group.name).toBe('Read')
    const input = group.input as Record<string, unknown>
    expect(input._grouped).toBe(true)
    expect((input._tools as Message[])).toHaveLength(3)
    expect(group.duration_ms).toBe(600)
  })

  it('groups 5+ consecutive same-tool calls', () => {
    const msgs = [
      makeTool('Write', 1),
      makeTool('Write', 2),
      makeTool('Write', 3),
      makeTool('Write', 4),
      makeTool('Write', 5),
    ]
    const result = groupConsecutiveTools(msgs)
    expect(result).toHaveLength(1)
    const input = result[0].input as Record<string, unknown>
    expect((input._tools as Message[])).toHaveLength(5)
  })

  it('does not group different tool names together', () => {
    const msgs = [
      makeTool('Read', 1),
      makeTool('Read', 2),
      makeTool('Bash', 3),
      makeTool('Bash', 4),
      makeTool('Bash', 5),
    ]
    const result = groupConsecutiveTools(msgs)
    expect(result).toHaveLength(3)
    expect(result[0].type).toBe('tool_use')
    expect(result[1].type).toBe('tool_use')
    expect(result[2].name).toBe('Bash')
  })

  it('does not group tool calls separated by text messages', () => {
    const msgs = [
      makeTool('Read', 1),
      makeTool('Read', 2),
      makeAssistant(3),
      makeTool('Read', 4),
      makeTool('Read', 5),
      makeTool('Read', 6),
    ]
    const result = groupConsecutiveTools(msgs)
    expect(result).toHaveLength(4)
    expect((result[3].input as Record<string, unknown>)._grouped).toBe(true)
    expect(((result[3].input as Record<string, unknown>)._tools as Message[])).toHaveLength(3)
  })

  it('preserves non-tool messages unchanged', () => {
    const msgs = [makeUser(1), makeAssistant(2), makeUser(3)]
    const result = groupConsecutiveTools(msgs)
    expect(result).toEqual(msgs)
  })

  it('handles empty array', () => {
    expect(groupConsecutiveTools([])).toEqual([])
  })

  it('sums duration_ms correctly for grouped tools', () => {
    const msgs = [
      makeTool('Bash', 1, 1500),
      makeTool('Bash', 2, 2500),
      makeTool('Bash', 3, 0),
      makeTool('Bash', 4, 1000),
    ]
    const result = groupConsecutiveTools(msgs)
    expect(result).toHaveLength(1)
    expect(result[0].duration_ms).toBe(5000)
  })
})

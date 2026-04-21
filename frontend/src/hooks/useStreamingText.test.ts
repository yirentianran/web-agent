import { describe, it, expect, beforeEach } from 'vitest'
import { useStreamingText } from './useStreamingText'

describe('useStreamingText', () => {
  let state: ReturnType<typeof useStreamingText.getState>

  beforeEach(() => {
    state = useStreamingText.createInitialState()
  })

  describe('getState / setState', () => {
    it('starts with empty accumulated text', () => {
      expect(state.accumulatedText).toBe('')
    })

    it('starts with null streaming message id', () => {
      expect(state.streamingMessageId).toBeNull()
    })
  })

  describe('processMessage', () => {
    it('accumulates text from content_block_delta events', () => {
      const delta1 = {
        type: 'stream_event',
        event: {
          type: 'content_block_delta',
          delta: { type: 'text_delta', text: 'Hello' },
        },
      } as any

      const result1 = useStreamingText.processMessage(state, delta1)
      expect(result1.accumulatedText).toBe('Hello')

      const delta2 = {
        type: 'stream_event',
        event: {
          type: 'content_block_delta',
          delta: { type: 'text_delta', text: ' world' },
        },
      } as any

      const result2 = useStreamingText.processMessage(result1, delta2)
      expect(result2.accumulatedText).toBe('Hello world')
    })

    it('ignores non-text_delta events', () => {
      const msg = {
        type: 'stream_event',
        event: {
          type: 'content_block_delta',
          delta: { type: 'input_json_delta', partial_json: '{}' },
        },
      } as any

      const result = useStreamingText.processMessage(state, msg)
      expect(result.accumulatedText).toBe('')
    })

    it('ignores non-content_block_delta stream events', () => {
      const msg = {
        type: 'stream_event',
        event: {
          type: 'tool_use',
          tool_name: 'Read',
        },
      } as any

      const result = useStreamingText.processMessage(state, msg)
      expect(result.accumulatedText).toBe('')
    })

    it('ignores non-stream_event messages', () => {
      const msg = {
        type: 'assistant',
        content: 'Hello world',
      } as any

      const result = useStreamingText.processMessage(state, msg)
      expect(result.accumulatedText).toBe('')
    })

    it('tracks streaming message index', () => {
      const delta = {
        type: 'stream_event',
        index: 42,
        event: {
          type: 'content_block_delta',
          delta: { type: 'text_delta', text: 'Test' },
        },
      } as any

      const result = useStreamingText.processMessage(state, delta)
      expect(result.streamingMessageId).toBe(42)
    })

    it('clears accumulated text when assistant message arrives with matching index', () => {
      // First accumulate some text with index 42
      const delta = {
        type: 'stream_event',
        index: 42,
        event: {
          type: 'content_block_delta',
          delta: { type: 'text_delta', text: 'Streaming text' },
        },
      } as any

      const afterDelta = useStreamingText.processMessage(state, delta)
      expect(afterDelta.accumulatedText).toBe('Streaming text')
      expect(afterDelta.streamingMessageId).toBe(42)

      // Then receive the complete assistant message with same index
      const assistant = {
        type: 'assistant',
        index: 42,
        content: 'Streaming text complete',
      } as any

      const result = useStreamingText.processMessage(afterDelta, assistant)
      expect(result.accumulatedText).toBe('')
      expect(result.streamingMessageId).toBeNull()
    })

    it('does not clear if assistant index differs from streaming index', () => {
      // Accumulate with index 42
      const delta = {
        type: 'stream_event',
        index: 42,
        event: {
          type: 'content_block_delta',
          delta: { type: 'text_delta', text: 'Test' },
        },
      } as any

      const afterDelta = useStreamingText.processMessage(state, delta)

      // Receive assistant with different index (should NOT clear)
      const assistant = {
        type: 'assistant',
        index: 40,
        content: 'Different message',
      } as any

      const result = useStreamingText.processMessage(afterDelta, assistant)
      expect(result.accumulatedText).toBe('Test')
    })
  })

  describe('reset', () => {
    it('clears accumulated text and message id', () => {
      const delta = {
        type: 'stream_event',
        index: 42,
        event: {
          type: 'content_block_delta',
          delta: { type: 'text_delta', text: 'Test' },
        },
      } as any

      const afterDelta = useStreamingText.processMessage(state, delta)
      expect(afterDelta.accumulatedText).toBe('Test')

      const resetState = useStreamingText.reset(afterDelta)
      expect(resetState.accumulatedText).toBe('')
      expect(resetState.streamingMessageId).toBeNull()
    })
  })
})
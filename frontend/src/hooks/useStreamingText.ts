/**
 * Streaming text aggregation state management.
 *
 * Handles accumulation of text from content_block_delta stream events
 * and cleanup when the complete assistant message arrives.
 */

export interface StreamingTextState {
  accumulatedText: string
  streamingMessageId: number | null
}

export interface UseStreamingText {
  getState: () => StreamingTextState
  setState: (state: StreamingTextState) => void
  processMessage: (msg: any) => void
  reset: () => void
}

/**
 * Create initial streaming text state.
 */
export function createInitialState(): StreamingTextState {
  return {
    accumulatedText: '',
    streamingMessageId: null,
  }
}

/**
 * Process a message and update streaming text state.
 *
 * - Accumulates text from content_block_delta stream events
 * - Clears text when matching assistant message arrives
 */
export function processMessage(
  state: StreamingTextState,
  msg: any
): StreamingTextState {
  // Handle stream_event.content_block_delta
  if (msg.type === 'stream_event' && msg.event?.type === 'content_block_delta') {
    const delta = msg.event.delta
    if (delta?.type === 'text_delta' && delta.text) {
      const newIndex = msg.index ?? state.streamingMessageId
      return {
        accumulatedText: state.accumulatedText + delta.text,
        streamingMessageId: newIndex,
      }
    }
  }

  // Handle assistant message — clear if matching index
  if (msg.type === 'assistant' && msg.index != null) {
    if (state.streamingMessageId === msg.index) {
      return createInitialState()
    }
  }

  return state
}

/**
 * Reset streaming text state to initial values.
 */
export function reset(state: StreamingTextState): StreamingTextState {
  return createInitialState()
}

/**
 * Export namespace for test access without React hook overhead.
 */
export const useStreamingText = {
  createInitialState,
  processMessage,
  reset,
}
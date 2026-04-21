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
 * - Clears text when message stops or non-streaming message arrives
 */
export function processMessage(
  state: StreamingTextState,
  msg: any
): StreamingTextState {
  // Handle stream_event.content_block_delta - accumulate text
  if (msg.type === 'stream_event' && msg.event?.type === 'content_block_delta') {
    const delta = msg.event.delta
    if (delta?.type === 'text_delta' && delta.text) {
      return {
        accumulatedText: state.accumulatedText + delta.text,
        streamingMessageId: state.streamingMessageId, // Keep existing
      }
    }
  }

  // Clear streaming text when message stops or assistant message completes
  // message_stop event signals end of streaming for this response
  if (msg.type === 'stream_event' && msg.event?.type === 'message_stop') {
    return createInitialState()
  }

  // Clear when assistant message arrives (complete, not streaming)
  if (msg.type === 'assistant' && msg.content) {
    return createInitialState()
  }

  // Clear when result message arrives (agent finished)
  if (msg.type === 'result') {
    return createInitialState()
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
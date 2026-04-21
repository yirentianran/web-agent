/**
 * Streaming text aggregation state management.
 *
 * Handles accumulation of text from content_block_delta stream events
 * and cleanup when the complete assistant message arrives.
 */
import type { Message } from "../lib/types";

export interface StreamingTextState {
  accumulatedText: string;
  streamingMessageId: number | null;
}

/** Narrowed shape of a content_block_delta stream event. */
interface TextDelta {
  type: "text_delta";
  text: string;
}

interface ContentBlockDelta {
  type: "content_block_delta";
  delta?: TextDelta | { type: string; [key: string]: unknown };
}

interface MessageStopEvent {
  type: "message_stop";
}

type StreamEvent =
  | ContentBlockDelta
  | MessageStopEvent
  | { type: string; [key: string]: unknown };

function isStreamEvent(
  msg: unknown,
): msg is Message & { type: "stream_event"; event?: StreamEvent } {
  return (
    typeof msg === "object" &&
    msg !== null &&
    (msg as Record<string, unknown>).type === "stream_event"
  );
}

function isTextDelta(
  event: StreamEvent | undefined,
): event is ContentBlockDelta {
  return event?.type === "content_block_delta" && "delta" in event;
}

function isMessageStop(event: StreamEvent | undefined): boolean {
  return event?.type === "message_stop";
}

function isAssistantMessage(
  msg: unknown,
): msg is Message & { type: "assistant"; content?: string } {
  return (
    typeof msg === "object" &&
    msg !== null &&
    (msg as Record<string, unknown>).type === "assistant"
  );
}

function isResultMessage(msg: unknown): msg is Message & { type: "result" } {
  return (
    typeof msg === "object" &&
    msg !== null &&
    (msg as Record<string, unknown>).type === "result"
  );
}

export interface UseStreamingText {
  getState: () => StreamingTextState;
  setState: (state: StreamingTextState) => void;
  processMessage: (msg: unknown) => void;
  reset: () => void;
}

/**
 * Create initial streaming text state.
 */
export function createInitialState(): StreamingTextState {
  return {
    accumulatedText: "",
    streamingMessageId: null,
  };
}

/**
 * Process a message and update streaming text state.
 *
 * - Accumulates text from content_block_delta stream events
 * - Clears text when message stops or non-streaming message arrives
 */
export function processMessage(
  state: StreamingTextState,
  msg: unknown,
): StreamingTextState {
  // Handle stream_event.content_block_delta - accumulate text
  if (isStreamEvent(msg) && isTextDelta(msg.event)) {
    const delta = msg.event.delta;
    if (delta?.type === "text_delta" && delta.text) {
      return {
        accumulatedText: state.accumulatedText + delta.text,
        streamingMessageId: state.streamingMessageId,
      };
    }
  }

  // Clear streaming text when message_stop event arrives
  if (isStreamEvent(msg) && isMessageStop(msg.event)) {
    return createInitialState();
  }

  // Clear when assistant message arrives (complete, not streaming)
  if (isAssistantMessage(msg) && msg.content) {
    return createInitialState();
  }

  // Clear when result message arrives (agent finished)
  if (isResultMessage(msg)) {
    return createInitialState();
  }

  return state;
}

/**
 * Reset streaming text state to initial values.
 */
export function reset(): StreamingTextState {
  return createInitialState();
}

/**
 * Export namespace for test access without React hook overhead.
 */
export const useStreamingText = {
  createInitialState,
  processMessage,
  reset,
};

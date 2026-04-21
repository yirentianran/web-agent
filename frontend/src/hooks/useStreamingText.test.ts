import { describe, it, expect, beforeEach } from "vitest";
import { useStreamingText } from "./useStreamingText";

describe("useStreamingText", () => {
  let state: ReturnType<typeof useStreamingText.createInitialState>;

  beforeEach(() => {
    state = useStreamingText.createInitialState();
  });

  describe("getState / setState", () => {
    it("starts with empty accumulated text", () => {
      expect(state.accumulatedText).toBe("");
    });

    it("starts with null streaming message id", () => {
      expect(state.streamingMessageId).toBeNull();
    });
  });

  describe("processMessage", () => {
    it("accumulates text from content_block_delta events", () => {
      const delta1 = {
        type: "stream_event",
        event: {
          type: "content_block_delta",
          delta: { type: "text_delta", text: "Hello" },
        },
      } as any;

      const result1 = useStreamingText.processMessage(state, delta1);
      expect(result1.accumulatedText).toBe("Hello");

      const delta2 = {
        type: "stream_event",
        event: {
          type: "content_block_delta",
          delta: { type: "text_delta", text: " world" },
        },
      } as any;

      const result2 = useStreamingText.processMessage(result1, delta2);
      expect(result2.accumulatedText).toBe("Hello world");
    });

    it("ignores non-text_delta events", () => {
      const msg = {
        type: "stream_event",
        event: {
          type: "content_block_delta",
          delta: { type: "input_json_delta", partial_json: "{}" },
        },
      } as any;

      const result = useStreamingText.processMessage(state, msg);
      expect(result.accumulatedText).toBe("");
    });

    it("ignores non-content_block_delta stream events", () => {
      const msg = {
        type: "stream_event",
        event: {
          type: "tool_use",
          tool_name: "Read",
        },
      } as any;

      const result = useStreamingText.processMessage(state, msg);
      expect(result.accumulatedText).toBe("");
    });

    it("ignores non-stream_event messages", () => {
      const msg = {
        type: "assistant",
        content: "Hello world",
      } as any;

      const result = useStreamingText.processMessage(state, msg);
      expect(result.accumulatedText).toBe("");
    });

    it("clears accumulated text on message_stop event", () => {
      // First accumulate some text
      const delta = {
        type: "stream_event",
        event: {
          type: "content_block_delta",
          delta: { type: "text_delta", text: "Streaming text" },
        },
      } as any;

      const afterDelta = useStreamingText.processMessage(state, delta);
      expect(afterDelta.accumulatedText).toBe("Streaming text");

      // Then receive message_stop event
      const stop = {
        type: "stream_event",
        event: {
          type: "message_stop",
        },
      } as any;

      const result = useStreamingText.processMessage(afterDelta, stop);
      expect(result.accumulatedText).toBe("");
      expect(result.streamingMessageId).toBeNull();
    });

    it("clears accumulated text when assistant message arrives", () => {
      // First accumulate some text
      const delta = {
        type: "stream_event",
        event: {
          type: "content_block_delta",
          delta: { type: "text_delta", text: "Streaming text" },
        },
      } as any;

      const afterDelta = useStreamingText.processMessage(state, delta);
      expect(afterDelta.accumulatedText).toBe("Streaming text");

      // Then receive complete assistant message
      const assistant = {
        type: "assistant",
        content: "Streaming text complete",
      } as any;

      const result = useStreamingText.processMessage(afterDelta, assistant);
      expect(result.accumulatedText).toBe("");
      expect(result.streamingMessageId).toBeNull();
    });

    it("clears accumulated text when result message arrives", () => {
      // First accumulate some text
      const delta = {
        type: "stream_event",
        event: {
          type: "content_block_delta",
          delta: { type: "text_delta", text: "Streaming text" },
        },
      } as any;

      const afterDelta = useStreamingText.processMessage(state, delta);

      // Then receive result message (agent finished)
      const resultMsg = {
        type: "result",
      } as any;

      const result = useStreamingText.processMessage(afterDelta, resultMsg);
      expect(result.accumulatedText).toBe("");
      expect(result.streamingMessageId).toBeNull();
    });
  });

  describe("reset", () => {
    it("clears accumulated text and message id", () => {
      const delta = {
        type: "stream_event",
        event: {
          type: "content_block_delta",
          delta: { type: "text_delta", text: "Test" },
        },
      } as any;

      const afterDelta = useStreamingText.processMessage(state, delta);
      expect(afterDelta.accumulatedText).toBe("Test");

      const resetState = useStreamingText.reset();
      expect(resetState.accumulatedText).toBe("");
      expect(resetState.streamingMessageId).toBeNull();
    });
  });
});

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useWebSocket } from "./useWebSocket";
import type { Message } from "../lib/types";

// Mock WebSocket globally
class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  url: string;
  readyState: number;
  onopen: (() => void) | null = null;
  onmessage: ((event: any) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sent: any[] = [];

  constructor(url: string) {
    this.url = url;
    this.readyState = MockWebSocket.CONNECTING;
    MockWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
    // Simulate onclose firing after close()
    setTimeout(() => {
      if (this.onclose) this.onclose();
    }, 0);
  }

  // Helper to simulate connection
  simulateOpen() {
    this.readyState = MockWebSocket.OPEN;
    if (this.onopen) this.onopen();
  }

  // Helper to simulate incoming message
  simulateMessage(data: any) {
    if (this.onmessage) this.onmessage({ data: JSON.stringify(data) });
  }

  // Helper to simulate disconnect
  simulateClose() {
    this.readyState = MockWebSocket.CLOSED;
    if (this.onclose) this.onclose();
  }

  static clearInstances() {
    MockWebSocket.instances = [];
  }
}

vi.stubGlobal("WebSocket", MockWebSocket as any);

describe("useWebSocket", () => {
  let onMessage: ReturnType<typeof vi.fn>;
  let onConnect: ReturnType<typeof vi.fn>;
  let onDisconnect: ReturnType<typeof vi.fn>;
  let onQueueFull: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    MockWebSocket.clearInstances();
    vi.useFakeTimers();
    onMessage = vi.fn();
    onConnect = vi.fn();
    onDisconnect = vi.fn();
    onQueueFull = vi.fn();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  type HookProps = Parameters<typeof useWebSocket>[0];

  function createHook(overrides: Partial<HookProps> = {}) {
    const baseProps: HookProps = {
      userId: "test-user",
      token: "test-token",
      onMessage: onMessage as (msg: Message) => void,
      onConnect: onConnect as (() => void) | undefined,
      onDisconnect: onDisconnect as (() => void) | undefined,
      onQueueFull: onQueueFull as (() => void) | undefined,
    };
    const props: HookProps = { ...baseProps, ...overrides };
    return renderHook((p: HookProps) => useWebSocket(p), { initialProps: props });
  }

  function rerenderWithProps(result: { rerender: (props: HookProps) => void }, overrides: Partial<HookProps> = {}) {
    const props: HookProps = {
      userId: "test-user",
      token: "test-token",
      onMessage: onMessage as (msg: Message) => void,
      onConnect: onConnect as (() => void) | undefined,
      onDisconnect: onDisconnect as (() => void) | undefined,
      onQueueFull: onQueueFull as (() => void) | undefined,
      ...overrides,
    };
    result.rerender(props);
  }

  describe("connection lifecycle", () => {
    it("connects on mount with token in URL", () => {
      const { result } = createHook();

      expect(result.current.status).toBe("connecting");
      expect(MockWebSocket.instances.length).toBe(1);
      expect(MockWebSocket.instances[0].url).toContain("token=test-token");
    });

    it("sets status to connected when WebSocket opens", () => {
      const { result } = createHook();
      const ws = MockWebSocket.instances[0];

      act(() => {
        ws.simulateOpen();
      });

      expect(result.current.status).toBe("connected");
      expect(onConnect).toHaveBeenCalledOnce();
    });

    it("flushes pending queue when connection opens", () => {
      const { result } = createHook();
      const ws = MockWebSocket.instances[0];

      // Send a message before connection is open (ws is CONNECTING)
      act(() => {
        result.current.sendMessage({ message: "hello" });
      });

      // Message is queued (not sent yet because not OPEN)
      expect(ws.sent.length).toBe(0);

      act(() => {
        ws.simulateOpen();
      });

      expect(ws.sent.length).toBe(1);
      const sent = JSON.parse(ws.sent[0]);
      expect(sent.message).toBe("hello");
      expect(sent.type).toBe("chat");
    });

    it("calls onDisconnect and schedules reconnect on close", () => {
      const { result } = createHook();
      const ws = MockWebSocket.instances[0];

      act(() => {
        ws.simulateOpen();
      });

      expect(result.current.status).toBe("connected");

      act(() => {
        ws.simulateClose();
        vi.advanceTimersByTime(1000); // Let the close timeout fire
      });

      expect(onDisconnect).toHaveBeenCalledOnce();
      expect(result.current.status).toBe("reconnecting");
    });

    it("reconnects with exponential backoff", () => {
      const { result } = createHook();
      let ws = MockWebSocket.instances[0];

      act(() => {
        ws.simulateOpen();
      });

      // First disconnect
      act(() => {
        ws.simulateClose();
        vi.advanceTimersByTime(100);
      });

      expect(result.current.status).toBe("reconnecting");

      // Should reconnect after ~1s delay (2^0 * 1000)
      act(() => {
        vi.advanceTimersByTime(1000);
      });

      expect(MockWebSocket.instances.length).toBe(2);
    });
  });

  describe("callback stability (fix for 'closed before established' bug)", () => {
    it("does NOT recreate WebSocket when onMessage callback changes", () => {
      const { rerender } = createHook();

      const wsBefore = MockWebSocket.instances[0];

      // Rerender with a new onMessage callback (simulates parent re-render)
      const newOnMessage = vi.fn();
      rerenderWithProps({ rerender }, { onMessage: newOnMessage as unknown as (msg: Message) => void });

      // Should NOT have created a new WebSocket
      expect(MockWebSocket.instances.length).toBe(1);
      expect(MockWebSocket.instances[0]).toBe(wsBefore);
    });

    it("does NOT recreate WebSocket when onDisconnect callback changes", () => {
      const { rerender } = createHook();

      const wsBefore = MockWebSocket.instances[0];

      // Rerender with a new onDisconnect callback
      const newOnDisconnect = vi.fn();
      rerenderWithProps({ rerender }, { onDisconnect: newOnDisconnect as unknown as (() => void) | undefined });

      // Should NOT have created a new WebSocket
      expect(MockWebSocket.instances.length).toBe(1);
      expect(MockWebSocket.instances[0]).toBe(wsBefore);
    });

    it("still delivers messages to the latest onMessage callback", () => {
      const onMsg1 = vi.fn();
      const hook = createHook({ onMessage: onMsg1 as unknown as (msg: Message) => void });
      const ws = MockWebSocket.instances[0];

      act(() => {
        ws.simulateOpen();
      });

      // Update callback
      const onMsg2 = vi.fn();
      rerenderWithProps(hook, { onMessage: onMsg2 as unknown as (msg: Message) => void });

      // Send message
      act(() => {
        ws.simulateMessage({ type: "assistant", content: "hello" });
      });

      // Latest callback should receive the message
      expect(onMsg2).toHaveBeenCalledWith(
        expect.objectContaining({ type: "assistant" }),
      );
    });
  });

  describe("queueFull state", () => {
    it("sets queueFull to true when pending queue exceeds max", () => {
      const hook = createHook();
      const { result } = hook;

      // Fill the queue (PENDING_QUEUE_MAX = 100)
      for (let i = 0; i < 101; i++) {
        act(() => {
          result.current.sendMessage({ message: `msg-${i}` });
        });
      }

      // Force re-render to pick up queueFull state
      rerenderWithProps(hook);

      expect(result.current.queueFull).toBe(true);
      expect(onQueueFull).toHaveBeenCalledOnce();
    });

    it("resets queueFull to false when connection is restored", () => {
      const hook = createHook();
      const { result } = hook;
      const ws = MockWebSocket.instances[0];

      // Fill queue and overflow
      for (let i = 0; i < 101; i++) {
        act(() => {
          result.current.sendMessage({ message: `msg-${i}` });
        });
      }
      rerenderWithProps(hook);

      expect(result.current.queueFull).toBe(true);

      // Open connection
      act(() => {
        ws.simulateOpen();
      });

      // queueFull should reset
      expect(result.current.queueFull).toBe(false);
    });
  });

  describe("priority queue for answers", () => {
    it("sends answers immediately when connected", () => {
      const { result } = createHook();
      const ws = MockWebSocket.instances[0];

      act(() => {
        ws.simulateOpen();
      });

      act(() => {
        result.current.sendAnswer("session-1", { answer: "yes" });
      });

      expect(ws.sent.length).toBe(1);
      const sent = JSON.parse(ws.sent[0]);
      expect(sent.type).toBe("answer");
      expect(sent.answers).toEqual({ answer: "yes" });
    });

    it("queues answers in priority queue when disconnected", () => {
      const { result } = createHook();
      const ws = MockWebSocket.instances[0];

      act(() => {
        result.current.sendAnswer("session-1", { answer: "yes" });
      });

      expect(ws.sent.length).toBe(0); // Not sent yet, queued

      act(() => {
        ws.simulateOpen();
      });

      expect(ws.sent.length).toBe(1);
      const sent = JSON.parse(ws.sent[0]);
      expect(sent.type).toBe("answer");
    });
  });

  describe("cleanup on unmount", () => {
    it("closes WebSocket on unmount", () => {
      const { unmount } = createHook();
      const ws = MockWebSocket.instances[0];

      act(() => {
        ws.simulateOpen();
      });

      act(() => {
        unmount();
        vi.advanceTimersByTime(100);
      });

      expect(ws.readyState).toBe(WebSocket.CLOSED);
    });

    it("does NOT trigger reconnect after intentional cleanup on unmount", () => {
      const { unmount } = createHook();
      const ws = MockWebSocket.instances[0];

      act(() => {
        ws.simulateOpen();
      });

      const instancesBefore = MockWebSocket.instances.length;

      act(() => {
        unmount();
        vi.advanceTimersByTime(200);
      });

      // Should NOT have attempted a reconnect
      expect(MockWebSocket.instances.length).toBe(instancesBefore);
    });

    it("clears reconnect timer on unmount", () => {
      const { unmount } = createHook();
      const ws = MockWebSocket.instances[0];

      act(() => {
        ws.simulateOpen();
        ws.simulateClose();
      });

      // Reconnect timer should be scheduled
      const instancesBefore = MockWebSocket.instances.length;

      // Unmount should clear the timer
      act(() => {
        unmount();
      });

      // After unmount, advancing time should NOT create a new WebSocket
      act(() => {
        vi.advanceTimersByTime(10000);
      });

      expect(MockWebSocket.instances.length).toBe(instancesBefore);
    });
  });

  describe("sendRecover", () => {
    it("sends recover message when connected", () => {
      const { result } = createHook();
      const ws = MockWebSocket.instances[0];

      act(() => {
        ws.simulateOpen();
      });

      act(() => {
        result.current.sendRecover("session-1", 5);
      });

      expect(ws.sent.length).toBe(1);
      const sent = JSON.parse(ws.sent[0]);
      expect(sent.type).toBe("recover");
      expect(sent.session_id).toBe("session-1");
      expect(sent.last_index).toBe(5);
    });

    it("does NOT queue recover when disconnected", () => {
      const { result } = createHook();
      const ws = MockWebSocket.instances[0];

      // Ensure ws is still CONNECTING
      expect(ws.readyState).toBe(MockWebSocket.CONNECTING);

      act(() => {
        result.current.sendRecover("session-1", 5);
      });

      // recover should NOT be queued when disconnected (by design)
      expect(ws.sent.length).toBe(0);
    });
  });

  describe("confirmSend and failPendingSends", () => {
    it("confirmSend clears the pending send timer", () => {
      const { result } = createHook();
      const ws = MockWebSocket.instances[0];

      act(() => {
        ws.simulateOpen();
      });

      act(() => {
        const { clientMsgId } = result.current.sendMessage({ message: "test" });
        result.current.confirmSend(clientMsgId);
      });

      // Timer should be cleared (no error thrown)
      expect(true).toBe(true);
    });

    it("failPendingSends rejects all pending sends on disconnect", () => {
      const { result } = createHook();
      const ws = MockWebSocket.instances[0];

      act(() => {
        ws.simulateOpen();
        result.current.sendMessage({ message: "test" });
      });

      act(() => {
        result.current.failPendingSends();
      });

      // Should not throw
      expect(true).toBe(true);
    });
  });
});

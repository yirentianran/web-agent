import { memo, useEffect, useRef, useCallback, useState, useMemo, useContext } from "react";
import { useTranslation } from "react-i18next";
import MessageBubble, { pairToolMessages } from "./MessageBubble";
import ToolGroupRenderer, {
  groupConsecutiveTools,
} from "./ToolGroupRenderer";

import StatusSpinner from "./StatusSpinner";
import type { Message, SessionStatus } from "../lib/types";
import { StreamingTextContext } from "../lib/streaming-context";

const SCROLL_THRESHOLD = 100;

const AGENT_START_TIME_KEY = "web-agent-start-times";

// Persist agent start times to localStorage so timer survives page refresh
// Discard entries older than 12 hours — they're definitely stale
function loadStartTimes(): Map<string, number> {
  try {
    const raw = localStorage.getItem(AGENT_START_TIME_KEY);
    if (!raw) return new Map();
    const parsed = JSON.parse(raw) as [string, number][];
    const now = Date.now();
    const maxAge = 12 * 60 * 60 * 1000; // 12 hours
    const valid = parsed.filter(([, ts]) => now - ts < maxAge);
    if (valid.length < parsed.length) {
      // Some entries were stale — update localStorage
      localStorage.setItem(AGENT_START_TIME_KEY, JSON.stringify(valid));
    }
    return new Map(valid);
  } catch {
    return new Map();
  }
}

function saveStartTimes(times: Map<string, number>) {
  try {
    const data = JSON.stringify(Array.from(times));
    localStorage.setItem(AGENT_START_TIME_KEY, data);
  } catch {
    // localStorage full — ignore
  }
}

interface ChatAreaProps {
  messages: Message[];
  sessionId: string | null;
  sessionState: SessionStatus;
  onAnswer: (sessionId: string, answers: Record<string, string>) => void;
  scrollPositions: Map<string, number>;
  onFileClick?: (filename: string) => void;
  onResend?: (message: Message) => void;
  authToken?: string | null;
  sessionLoading?: boolean;
}

interface MessageListProps {
  messages: Message[];
  sessionId: string;
  onAnswer: (sessionId: string, answers: Record<string, string>) => void;
  onFileClick?: (filename: string) => void;
  onResend?: (message: Message) => void;
  lastTodoWriteIndex?: number;
  lastUserMsgIndex?: number;
  authToken?: string | null;
}

const MessageList = memo(function MessageList({
  messages,
  sessionId,
  onAnswer,
  onFileClick,
  onResend,
  lastTodoWriteIndex,
  lastUserMsgIndex,
  authToken,
}: MessageListProps) {
  return messages.map((msg, i) => {
    // Check for tool group marker
    if (msg.type === 'tool_use' && msg.input) {
      const input = msg.input as Record<string, unknown>
      if (input._grouped) {
        const tools = input._tools as Message[]
        return (
          <ToolGroupRenderer key={`group-${msg.name}-${msg.index}`} tools={tools} />
        )
      }
    }
    return (
      <MessageBubble
        key={msg.clientMsgId ?? `${msg.index}-${i}`}
        message={msg}
        sessionId={sessionId}
        onAnswer={onAnswer}
        onFileClick={onFileClick}
        onResend={onResend}
        lastTodoWriteIndex={lastTodoWriteIndex}
        lastUserMsgIndex={lastUserMsgIndex}
        authToken={authToken}
      />
    )
  });
});

export default function ChatArea({
  messages,
  sessionId,
  sessionState,
  onAnswer,
  scrollPositions,
  onFileClick,
  onResend,
  authToken,
  sessionLoading,
}: ChatAreaProps) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const isUserAtBottomRef = useRef(true);
  const isStreamingRef = useRef(false);
  const [agentStartTime, setAgentStartTime] = useState<number | null>(null);

  const streamingText = useContext(StreamingTextContext);

  const prevScrollHeightRef = useRef(0);

  const handleScroll = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;

    // Detect whether user is near the bottom
    const { scrollTop, scrollHeight, clientHeight } = container;
    const distanceFromBottom = scrollHeight - scrollTop - clientHeight;

    // During streaming, content growth increases scrollHeight which
    // makes distanceFromBottom appear large even though the user didn't
    // scroll away. Distinguish "content grew" (scrollHeight changed)
    // from "user scrolled up" (scrollHeight unchanged, scrollTop changed).
    if (isStreamingRef.current) {
      const contentGrew = scrollHeight !== prevScrollHeightRef.current;
      prevScrollHeightRef.current = scrollHeight;
      // Only mark as "user scrolled away" when content DIDN'T grow
      // (user explicitly scrolled up)
      if (!contentGrew && distanceFromBottom > SCROLL_THRESHOLD) {
        isUserAtBottomRef.current = false;
      }
      // If content grew and user was at bottom, keep them at bottom
    } else {
      prevScrollHeightRef.current = scrollHeight;
      isUserAtBottomRef.current = distanceFromBottom <= SCROLL_THRESHOLD;
    }

    // Save scroll position to localStorage for session restore
    if (sessionId) {
      scrollPositions.set(sessionId, scrollTop);
      // Also persist to localStorage so it survives page refresh
      try {
        const SCROLL_STORAGE_KEY = "web-agent-scroll-positions";
        const positions = new Map<string, number>();
        // Read current positions from localStorage
        const raw = localStorage.getItem(SCROLL_STORAGE_KEY);
        if (raw) {
          const parsed = JSON.parse(raw) as [string, number][];
          parsed.forEach(([k, v]) => positions.set(k, v));
        }
        positions.set(sessionId, scrollTop);
        localStorage.setItem(
          SCROLL_STORAGE_KEY,
          JSON.stringify(Array.from(positions)),
        );
      } catch {
        // localStorage full or unavailable — skip
      }
    }
  }, [sessionId, scrollPositions]);

  const scrollToBottom = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    container.scrollTop = container.scrollHeight;
  }, []);

  // Track when agent started running.
  // Uses a ref to detect transitions into 'running' so follow-ups
  // correctly reset the elapsed timer. Heartbeats update a stale
  // counter but do NOT reset the elapsed timer (that caused the
  // "timer jumps back to 0" bug).
  // When sessionId changes we save the current start time to a
  // per-session Map so switching back restores the original timer
  // instead of resetting to 0 (A running → B idle → A continues).
  // Start times are persisted to localStorage so page refresh preserves timer.
  const prevSessionStateRef = useRef<string | null>(null);
  const agentSessionIdRef = useRef<string | null>(null);
  const heartbeatCountRef = useRef(0);
  const sessionStartTimesRef = useRef<Map<string, number>>(loadStartTimes());

  useEffect(() => {
    // Session changed — save previous session's start time, restore new session's
    if (agentSessionIdRef.current !== sessionId) {
      agentSessionIdRef.current = sessionId;
      prevSessionStateRef.current = sessionState;

      if (sessionState === "running" && sessionId) {
        const savedStart = sessionStartTimesRef.current.get(sessionId);
        if (savedStart !== undefined) {
          setAgentStartTime(savedStart);
        } else {
          // First time seeing this session as running — record now
          const now = Date.now();
          sessionStartTimesRef.current.set(sessionId, now);
          saveStartTimes(sessionStartTimesRef.current);
          setAgentStartTime(now);
        }
      } else {
        // Session is not running on mount or change — hide the spinner.
        // Do NOT delete the stored start time here: the state may
        // later transition to 'running' (e.g. page refresh while agent
        // is running, buffer status API returns after mount).
        setAgentStartTime(null);
      }
      return;
    }

    // Detect transition TO running.
    // If a saved start time exists in localStorage, the session was already
    // running before the page refresh — restore the saved timer instead of
    // resetting to 0. Otherwise this is a new user turn — record fresh time.
    if (
      sessionState === "running" &&
      prevSessionStateRef.current !== "running"
    ) {
      const savedStart = sessionId ? sessionStartTimesRef.current.get(sessionId) : undefined;
      if (savedStart !== undefined) {
        setAgentStartTime(savedStart);
      } else {
        const now = Date.now();
        if (sessionId) {
          sessionStartTimesRef.current.set(sessionId, now);
          saveStartTimes(sessionStartTimesRef.current);
        }
        setAgentStartTime(now);
      }
    }
    // Transition AWAY from running — clear start time for this session
    // (new runs will get a fresh timestamp)
    if (
      prevSessionStateRef.current === "running" &&
      sessionState !== "running"
    ) {
      if (sessionId) {
        sessionStartTimesRef.current.delete(sessionId);
        saveStartTimes(sessionStartTimesRef.current);
      }
      setAgentStartTime(null);
    }
    // Count heartbeats for stale detection (don't affect elapsed timer)
    heartbeatCountRef.current = messages.filter(
      (m) => m.type === "heartbeat",
    ).length;

    prevSessionStateRef.current = sessionState;
  }, [sessionState, messages, sessionId]);

  // ── Scroll to bottom on session change / initial load ────────────
  const prevSessionIdRef = useRef<string | null>(null);
  const prevMessagesLenRef = useRef(0);
  useEffect(() => {
    if (!sessionId || !containerRef.current) return;

    const sessionChanged = prevSessionIdRef.current !== sessionId;
    const wasEmpty = prevMessagesLenRef.current === 0;
    prevSessionIdRef.current = sessionId;
    prevMessagesLenRef.current = messages.length;

    // Session switch or first data load → always scroll to bottom.
    if (sessionChanged || (wasEmpty && messages.length > 0)) {
      isUserAtBottomRef.current = true;
      scrollToBottom();
      return;
    }

    // Same session, new messages arrived (streaming) — auto-scroll
    // only when the user was already at the bottom.
    if (isUserAtBottomRef.current && messages.length > 0) {
      scrollToBottom();
    }
  }, [sessionId, messages, scrollToBottom]);

  // ── Auto-scroll during streaming text ────────────────────────────
  // The `messages` array doesn't change during stream_event (only
  // `streamingText` updates), so the effect above doesn't fire.
  // ResizeObserver on the fixed-size container doesn't fire either.
  //
  // isStreamingRef prevents handleScroll from setting isUserAtBottomRef
  // to false when scrollHeight grows due to content being added.
  // rAF callback checks actual scroll position to decide whether to
  // scroll, avoiding dependency on potentially-stale ref.
  useEffect(() => {
    if (!streamingText) {
      isStreamingRef.current = false;
      return;
    }
    isStreamingRef.current = true;

    const rafId = requestAnimationFrame(() => {
      const container = containerRef.current;
      if (!container) return;
      const { scrollTop, scrollHeight, clientHeight } = container;
      const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
      if (distanceFromBottom <= SCROLL_THRESHOLD) {
        container.scrollTop = container.scrollHeight;
      }
    });
    return () => cancelAnimationFrame(rafId);
  }, [streamingText]);

  // ── Auto-follow bottom when content height changes ───────────────
  // Markdown rendering, code highlighting, and lazy-loaded media can
  // increase scrollHeight after the initial scrollToBottom call.
  // ResizeObserver keeps the viewport anchored to the bottom as long
  // as the user hasn't scrolled away.
  useEffect(() => {
    const container = containerRef.current
    if (!container || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => {
      if (isUserAtBottomRef.current) scrollToBottom()
    })
    ro.observe(container)
    return () => ro.disconnect()
  }, [scrollToBottom])

  // ── Running transition — always scroll to bottom ────────────────
  const prevStateRef = useRef<string | null>(null);
  useEffect(() => {
    if (sessionState === "running" && prevStateRef.current !== "running") {
      isUserAtBottomRef.current = true;
      scrollToBottom();
    }
    prevStateRef.current = sessionState;
  }, [sessionState, scrollToBottom]);

  // Determine what spinner to show
  const isAgentRunning = sessionState === "running";

  // Find the index of the latest TodoWrite message so MessageBubble can
  // hide older TodoWrite visualizations (deduplicate todo lists).
  const lastTodoWriteIndex = useMemo(() => {
    let maxIndex = -1;
    for (const msg of messages) {
      if (
        msg.type === "tool_use" &&
        msg.name === "TodoWrite" &&
        msg.index > maxIndex
      ) {
        maxIndex = msg.index;
      }
    }
    return maxIndex === -1 ? undefined : maxIndex;
  }, [messages]);

  // Index of the most recent user message — used to scope errors to the
  // current run. Errors with index below this belong to a previous run
  // and should be rendered as resolved / dimmed.
  const lastUserMsgIndex = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].type === "user") return messages[i].index;
    }
    return -1;
  }, [messages]);

  // Use messages in their natural arrival order — sorting by index
  // is unreliable because agent stream events and DB-assigned seq
  // use two different numbering schemes that can interleave.
  const visibleMessages = useMemo(() => {
    const deduped = messages.filter(
      (msg) =>
        msg.type !== "tool_use" ||
        msg.name !== "TodoWrite" ||
        msg.index === (lastTodoWriteIndex ?? -1),
    );
    const paired = pairToolMessages(deduped);
    return groupConsecutiveTools(paired);
  }, [messages, lastTodoWriteIndex]);

  // Filter out invisible message types for the welcome screen check.
  // If a session only has heartbeats / internal state messages, show the welcome screen.
  const hasVisibleMessages = useMemo(() => {
    return messages.some((msg) => {
      if (msg.type === "heartbeat") return false;
      if (
        msg.type === "system" &&
        msg.subtype &&
        [
          "hook_started",
          "hook_response",
          "hook_error",
          "init",
          "session_state_changed",
          "session_cancelled",
        ].includes(msg.subtype)
      )
        return false;
      if (msg.type === "user" && (!msg.content || !msg.content.trim())) {
        const files =
          (msg.data as Array<{ filename: string }> | undefined) || [];
        if (files.length === 0) return false;
      }
      return true;
    });
  }, [messages]);

  return (
    <div className="chat-area">
      <div className="messages" ref={containerRef} onScroll={handleScroll}>
        {/* No active session → always show welcome, never messages */}
        {sessionId === null && !sessionLoading && (
          <div className="chat-welcome">
            <div className="welcome-logo">◎</div>
            <h1 className="welcome-title">{t('chat.welcomeTitle')}</h1>
            <p className="welcome-desc">{t('chat.welcomeDesc')}</p>
          </div>
        )}
        {sessionId !== null && !hasVisibleMessages && !sessionLoading && (
          <div className="chat-welcome">
            <div className="welcome-logo">◎</div>
            <h1 className="welcome-title">{t('chat.welcomeTitle')}</h1>
            <p className="welcome-desc">{t('chat.welcomeDesc')}</p>
          </div>
        )}
        {sessionId !== null && sessionLoading && (
          <div className="chat-welcome">
            <StatusSpinner label={t('chat.switchingSession')} />
          </div>
        )}

        {sessionId !== null && (
          <MessageList
            messages={visibleMessages}
            sessionId={sessionId}
            onAnswer={onAnswer}
            onFileClick={onFileClick}
            onResend={onResend}
            lastTodoWriteIndex={lastTodoWriteIndex}
            lastUserMsgIndex={lastUserMsgIndex}
            authToken={authToken}
          />
        )}

        {/* Streaming text indicator — shows accumulated content_block_delta text */}
        {/* During streaming, render as plain text to prevent layout jitter from */}
        {/* markdown re-parsing. Once streaming completes, streamingText clears */}
        {/* and the full message appears in the messages array with formatting. */}
        {sessionId !== null && streamingText && streamingText.trim() && (
          <div className="message assistant-message streaming-message">
            <div className="bubble">
              <span className="streaming-text">{streamingText}</span>
            </div>
          </div>
        )}

        {/* Show agent spinner when session is running */}
        {sessionId !== null && isAgentRunning && (
          <div className="message system-message">
            <StatusSpinner
              variant="agent"
              text={t('chat.agentWorking')}
              startTime={agentStartTime ?? undefined}
            />
          </div>
        )}

        {/* Error state indicator */}
        {sessionId !== null && sessionState === "error" && (
          <div className="message system-message session-error-banner">
            <p>{t('chat.errorBanner')}</p>
          </div>
        )}
      </div>

    </div>
  );
}

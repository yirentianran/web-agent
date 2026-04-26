import { useEffect, useRef, useCallback, useState, useMemo } from "react";
import MessageBubble from "./MessageBubble";
import SkillFeedbackWidget from "./SkillFeedbackWidget";
import StatusSpinner from "./StatusSpinner";
import type { Message } from "../lib/types";

const SCROLL_THRESHOLD = 100; // pixels from bottom to consider "at bottom"

const TERMINAL_STATES = new Set(["completed", "error", "cancelled"]);

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
  sessionState: string;
  onAnswer: (sessionId: string, answers: Record<string, string>) => void;
  scrollPositions: Map<string, number>;
  onFileClick?: (filename: string) => void;
  authToken?: string | null;
  streamingText?: string; // Accumulated streaming text from content_block_delta
}

export default function ChatArea({
  messages,
  sessionId,
  sessionState,
  onAnswer,
  scrollPositions,
  onFileClick,
  authToken,
  streamingText,
}: ChatAreaProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const isUserAtBottomRef = useRef(true);
  const [agentStartTime, setAgentStartTime] = useState<number | null>(null);

  const handleScroll = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;

    // Detect whether user is near the bottom
    const { scrollTop, scrollHeight, clientHeight } = container;
    const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
    isUserAtBottomRef.current = distanceFromBottom <= SCROLL_THRESHOLD;

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
        // Session is not running — clean up any stale stored start time
        // so the next run gets a fresh timestamp instead of accumulating
        // time from a previous run.
        if (sessionId) {
          sessionStartTimesRef.current.delete(sessionId);
          saveStartTimes(sessionStartTimesRef.current);
        }
        setAgentStartTime(null);
      }
      return;
    }

    // Detect transition TO running — restore saved time if available
    if (
      sessionState === "running" &&
      prevSessionStateRef.current !== "running"
    ) {
      const savedStart = sessionId
        ? sessionStartTimesRef.current.get(sessionId)
        : undefined;
      if (savedStart !== undefined) {
        // If we're transitioning from a terminal state (completed/error/cancelled),
        // the saved start time is from a previous run — discard it so we don't
        // accumulate elapsed time across separate runs.
        const prevWasTerminal =
          prevSessionStateRef.current !== null &&
          TERMINAL_STATES.has(prevSessionStateRef.current);
        if (prevWasTerminal) {
          if (sessionId) {
            sessionStartTimesRef.current.delete(sessionId);
            saveStartTimes(sessionStartTimesRef.current);
          }
        } else {
          // Use saved start time from localStorage (preserves timer on page refresh)
          setAgentStartTime(savedStart);
          prevSessionStateRef.current = sessionState;
          return;
        }
      }
      // No valid saved time — record now
      const now = Date.now();
      if (sessionId) {
        sessionStartTimesRef.current.set(sessionId, now);
        saveStartTimes(sessionStartTimesRef.current);
      }
      setAgentStartTime(now);
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

  // Sort messages by index to ensure chronological order (newest at bottom)
  const sortedMessages = useMemo(
    () => [...messages].sort((a, b) => a.index - b.index),
    [messages],
  );

  // Keep only the latest TodoWrite message — hide all earlier updates.
  // TodoWrite is a stateful progress widget; showing every snapshot
  // creates stacked duplicate progress bars.
  const filteredMessages = useMemo(() => {
    let lastTodoWriteIndex = -1;
    for (let i = sortedMessages.length - 1; i >= 0; i--) {
      if (
        sortedMessages[i].type === "tool_use" &&
        sortedMessages[i].name === "TodoWrite"
      ) {
        lastTodoWriteIndex = sortedMessages[i].index;
        break;
      }
    }
    return sortedMessages.filter(
      (msg) =>
        msg.type !== "tool_use" ||
        msg.name !== "TodoWrite" ||
        msg.index === lastTodoWriteIndex,
    );
  }, [sortedMessages]);

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

  // Derive skill names from tool_use messages for feedback endpoint.
  // Collect all unique skill names; the widget handles single vs multi-skill display.
  const feedbackSkillNames = useMemo(() => {
    const skillTools = new Set<string>();
    for (const msg of messages) {
      if (msg.type === "tool_use" && msg.name) {
        skillTools.add(msg.name);
      }
    }
    return Array.from(skillTools);
  }, [messages]);

  return (
    <div className="chat-area">
      <div className="messages" ref={containerRef} onScroll={handleScroll}>
        {!hasVisibleMessages && (
          <div className="chat-welcome">
            <div className="welcome-logo">◎</div>
            <h1 className="welcome-title">Web Agent</h1>
            <p className="welcome-desc">Your AI-powered companion</p>
          </div>
        )}

        {filteredMessages.map((msg, i) => (
          <MessageBubble
            key={msg.clientMsgId ?? `${msg.index}-${i}`}
            message={msg}
            sessionId={sessionId || ""}
            onAnswer={onAnswer}
            onFileClick={onFileClick}
            lastTodoWriteIndex={lastTodoWriteIndex}
          />
        ))}

        {/* Streaming text indicator — shows accumulated content_block_delta text */}
        {/* Show streaming text WHILE agent is running for progressive display */}
        {streamingText && streamingText.trim() && (
          <div className="message assistant-message streaming-message">
            <div className="bubble">
              <span className="streaming-text">{streamingText}</span>
            </div>
          </div>
        )}

        {/* Show agent spinner when session is running */}
        {isAgentRunning && (
          <div className="message system-message">
            <StatusSpinner
              variant="agent"
              text="Agent is working..."
              startTime={agentStartTime ?? undefined}
            />
          </div>
        )}

        {/* Error state indicator */}
        {sessionState === "error" && (
          <div className="message system-message session-error-banner">
            <p>An error occurred while processing your request. Please try again.</p>
          </div>
        )}
      </div>

      {sessionState === "completed" && (
        <SkillFeedbackWidget
          skillNames={
            feedbackSkillNames.length > 0 ? feedbackSkillNames : undefined
          }
          onSubmit={async (rating, comment, userEdits, skillName) => {
            const headers: Record<string, string> = {
              "Content-Type": "application/json",
            };
            if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
            await fetch(`/api/skills/${skillName}/feedback`, {
              method: "POST",
              headers,
              body: JSON.stringify({
                rating,
                comment,
                user_edits: userEdits,
                session_id: sessionId,
              }),
            });
          }}
        />
      )}
    </div>
  );
}

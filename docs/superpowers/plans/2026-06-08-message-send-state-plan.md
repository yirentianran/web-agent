# Message Send State Simplification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the `sent` state from message send tracking, add precise failure detection (ws.send() throw, WebSocket death), and keep timeout as fallback.

**Architecture:** Four files changed. `MessageSendState` loses `'sent'`. `useWebSocket` gains try/catch on `ws.send()` and an `onConnectionFailed` callback. `App.tsx` gains `clearSendState` and wires `onConnectionFailed`. `MessageBubble` drops the checkmark icon. Confirmation clears the send indicator entirely instead of showing "✓".

**Tech Stack:** TypeScript, React, Vitest, Testing Library

---

### Task 1: Remove `'sent'` from `MessageSendState` type

**Files:**
- Modify: `frontend/src/lib/types.ts:34`

- [ ] **Step 1: Change the type definition**

```typescript
/** Send state machine for user messages */
export type MessageSendState = 'sending' | 'failed'
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/types.ts
git commit -m "refactor: remove 'sent' from MessageSendState type"
```

---

### Task 2: useWebSocket — add ws.send() try/catch and onConnectionFailed

**Files:**
- Modify: `frontend/src/hooks/useWebSocket.ts:44,289-315,224-236,398-409`

- [ ] **Step 1: Add `onConnectionFailed` to options interface (line 44)**

After `onSendFailed?: (clientMsgId: string) => void;`, add:

```typescript
onConnectionFailed?: (unconfirmedIds: string[]) => void;
```

- [ ] **Step 2: Add ref for onConnectionFailed (near line 73)**

After `const onSendFailedRef = useRef(onSendFailed);`, add:

```typescript
const onConnectionFailedRef = useRef(onConnectionFailed);
```

- [ ] **Step 3: Sync ref in the sync effect (near line 89)**

After `onSendFailedRef.current = onSendFailed;`, add:

```typescript
onConnectionFailedRef.current = onConnectionFailed;
```

- [ ] **Step 4: Wrap `ws.send()` in try/catch in `sendMessage` (lines 302-305)**

Replace:
```typescript
if (ws?.readyState === WebSocket.OPEN) {
  const payload = JSON.stringify({ type: "chat", ...enriched, user_id: userIdRef.current });
  logger.debug("Sending direct:", payload.slice(0, 100));
  ws.send(payload);
}
```

With:
```typescript
if (ws?.readyState === WebSocket.OPEN) {
  const payload = JSON.stringify({ type: "chat", ...enriched, user_id: userIdRef.current });
  logger.debug("Sending direct:", payload.slice(0, 100));
  try {
    ws.send(payload);
  } catch {
    // ws.send() can throw synchronously if the connection is in a bad state
    // or the data payload exceeds the browser's limits.
    onReject();
    return { clientMsgId };
  }
}
```

- [ ] **Step 5: Call `onConnectionFailed` when reconnect attempts exhausted (replace lines 224-231)**

Replace:
```typescript
if (reconnectAttempts.current >= maxAttempts) {
  setStatus("failed");
  for (const [, ps] of pendingSends.current) {
    ps.reject("connection_failed");
  }
  pendingSends.current.clear();
```

With:
```typescript
if (reconnectAttempts.current >= maxAttempts) {
  setStatus("failed");
  const unconfirmed = Array.from(pendingSends.current.keys());
  for (const [, ps] of pendingSends.current) {
    clearTimeout(ps.timer);
  }
  pendingSends.current.clear();
  if (unconfirmed.length > 0) {
    onConnectionFailedRef.current?.(unconfirmed);
  }
```

Note: The `ps.reject("connection_failed")` call is removed because it fires `onSendFailed` per-message, which individually sets session state to idle. The new `onConnectionFailed` handler batches this in App.tsx.

- [ ] **Step 6: Add `onConnectionFailed` to destructured params and return value**

In destructured params (line 50-60), add `onConnectionFailed`:

```typescript
export function useWebSocket({
  userId,
  onMessage,
  onConnect,
  onDisconnect,
  onQueueFull,
  onSendFailed,
  onConnectionFailed,
  onRecoverTimeout,
  onAuthFailed,
  token,
}: UseWebSocketOptions) {
```

In return value (line 398-409), add `onConnectionFailed` is not needed in return — the callback is consumed internally. No change needed.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/hooks/useWebSocket.ts
git commit -m "feat: add ws.send() try/catch and onConnectionFailed callback to useWebSocket"
```

---

### Task 3: App.tsx — clear send state on confirmation (replace all `"sent"` with clearing)

**Files:**
- Modify: `frontend/src/App.tsx:415-437,659-660,689-690,937-938,945-946,1091-1092,1126-1127`

- [ ] **Step 1: Add `clearSendState` helper (after `updateSendState` around line 844)**

```typescript
const clearSendState = useCallback(
  (clientMsgId: string | undefined) => {
    if (!clientMsgId) return;
    sendStateMapRef.current.delete(clientMsgId);
    setMessages((prev) =>
      prev.map((m) =>
        m.clientMsgId === clientMsgId ? { ...m, sendState: undefined } : m,
      ),
    );
  },
  [],
);
```

- [ ] **Step 2: Update `updateByClientMsgId` (line 415-424)**

Replace `sendState: "sent" as const` with `sendState: undefined`:

```typescript
const updateByClientMsgId = (
  prev: Message[],
  clientMsgId: string,
  newIndex: number | undefined,
): Message[] =>
  prev.map((m) =>
    m.clientMsgId === clientMsgId
      ? { ...m, index: newIndex ?? m.index, sendState: undefined }
      : m,
  );
```

- [ ] **Step 3: Update `applyEchoUpdate` (line 430-437)**

Replace both occurrences of `sendState: "sent" as const` with `sendState: undefined`:

```typescript
const applyEchoUpdate = (prev: Message[], msg: Message): Message[] =>
  prev.map((m) => {
    if (m.clientMsgId !== msg.clientMsgId) return m;
    if (msg.index != null && msg.index < (m.index ?? 0)) {
      return { ...m, sendState: undefined };
    }
    return { ...m, index: msg.index ?? m.index, sendState: undefined };
  });
```

- [ ] **Step 4: Update REST history confirmation (lines 658-660)**

Replace `updateSendState(m.clientMsgId, "sent")` with `clearSendState(m.clientMsgId)`:

```typescript
for (const m of msgs) {
  if (m.type === "user" && m.clientMsgId) {
    clearSendState(m.clientMsgId);
    confirmSendRef.current(m.clientMsgId);
    if (m.session_id) {
      pendingUserMsgsRef.current.delete(m.session_id);
      clearPendingMessage(m.session_id, userId);
    }
  }
}
```

- [ ] **Step 5: Update confirmedIndices filter (line 689-690)**

Replace:
```typescript
const confirmedIndices = new Set(
  sameSession.filter((m) => m.sendState === "sent" || !m.sendState).map((m) => m.index),
);
```

With:
```typescript
const confirmedIndices = new Set(
  sameSession.filter((m) => !m.sendState).map((m) => m.index),
);
```

- [ ] **Step 6: Update WebSocket echo confirmations (lines 937-938, 945-946)**

Replace `updateSendState(pending.clientMsgId, "sent")` with `clearSendState(pending.clientMsgId)`:

Line 937:
```typescript
clearSendState(pending.clientMsgId);
```

Line 945:
```typescript
clearSendState(msg.clientMsgId);
```

- [ ] **Step 7: Update content-match dedup (lines 1091-1092 and 1126-1127)**

Replace `sendState: "sent" as const` with `sendState: undefined` in both locations:

Line 1091-1092:
```typescript
m.type === "user" && m.content === msg.content && m.sendState === "sending"
  ? { ...m, index: msg.index ?? m.index, sendState: undefined }
```

Line 1126-1127:
```typescript
m.type === "user" && m.content === msg.content && m.sendState === "sending"
  ? { ...m, index: msg.index ?? m.index, sendState: undefined }
```

- [ ] **Step 8: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "refactor: clear sendState on confirmation instead of setting 'sent'"
```

---

### Task 4: MessageBubble — remove `sent` state rendering

**Files:**
- Modify: `frontend/src/components/MessageBubble.tsx:200-206`
- Modify: `frontend/src/styles/global.css:4584-4593`

- [ ] **Step 1: Remove the `sent` branch from sendStateIcon (lines 200-206)**

Replace:
```typescript
const sendStateIcon = message.sendState === 'sending'
  ? <span className="send-state send-state--sending" title={t('message.sending')} aria-label={t('message.sending')}>◌</span>
  : message.sendState === 'sent'
  ? <span className="send-state send-state--sent" title={t('message.sent')} aria-label={t('message.sent')}>✓</span>
  : message.sendState === 'failed'
  ? <span className="send-state send-state--failed" title={t('message.sendFailed')} aria-label={t('message.sendFailed')} role="button" tabIndex={0} onClick={() => onResend?.(message)}>✗</span>
  : null
```

With:
```typescript
const sendStateIcon = message.sendState === 'sending'
  ? <span className="send-state send-state--sending" title={t('message.sending')} aria-label={t('message.sending')}>◌</span>
  : message.sendState === 'failed'
  ? <span className="send-state send-state--failed" title={t('message.sendFailed')} aria-label={t('message.sendFailed')} role="button" tabIndex={0} onClick={() => onResend?.(message)}>✗</span>
  : null
```

- [ ] **Step 2: Remove `.send-state--sent` CSS and `send-sent-fade` keyframes (global.css lines 4584-4593)**

Remove:
```css
.send-state--sent {
  color: var(--color-success, #22c55e);
  animation: send-sent-fade 2.5s ease-out forwards;
}

@keyframes send-sent-fade {
  0% { opacity: 1; }
  70% { opacity: 1; }
  100% { opacity: 0; }
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/MessageBubble.tsx frontend/src/styles/global.css
git commit -m "refactor: remove 'sent' state indicator from MessageBubble"
```

---

### Task 5: App.tsx — wire onConnectionFailed to bulk-fail unconfirmed messages

**Files:**
- Modify: `frontend/src/App.tsx:1309-1343`

- [ ] **Step 1: Add `onConnectionFailed` handler in useWebSocket call (after line 1322)**

Add the handler inside the `useWebSocket` options object, after `onSendFailed: handleSendFailed,`:

```typescript
onConnectionFailed: (unconfirmedIds: string[]) => {
  logger.warn(
    "[WebSocket] Connection failed, marking %d unconfirmed messages as failed",
    unconfirmedIds.length,
  );
  for (const id of unconfirmedIds) {
    updateSendState(id, "failed");
  }
  // Reset session state for the active session if it was running
  const activeId = urlSessionIdRef.current;
  if (activeId && sessionStatesRef.current.get(activeId) === "running") {
    setSessionStateFor(activeId, "idle");
  }
},
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat: bulk-fail unconfirmed messages on WebSocket connection death"
```

---

### Task 6: Update tests for new behavior

**Files:**
- Modify: `frontend/src/App.test.ts:41,50,52`
- Modify: `frontend/src/hooks/useWebSocket.test.ts` (add ws.send() throw test)
- Modify: `frontend/src/components/MessageBubble.test.tsx` (add send state indicator tests)

- [ ] **Step 1: Update App.test.ts references to `sendState: 'sent'`**

Line 41:
```typescript
? { ...m, index: newIndex ?? m.index, sendState: undefined }
```

Line 50:
```typescript
return { ...m, sendState: undefined };
```

Line 52:
```typescript
return { ...m, index: msg.index ?? m.index, sendState: undefined };
```

- [ ] **Step 2: Add test for sendState icon behavior in MessageBubble (no "sent" icon)**

In `MessageBubble.test.tsx`, add:

```typescript
describe('MessageBubble - send state indicator', () => {
  it('shows sending indicator when sendState is "sending"', () => {
    const message: Message = {
      type: 'user',
      content: 'Hello',
      index: 0,
      sendState: 'sending',
      clientMsgId: 'uuid-1',
    }
    renderMessage(message)
    expect(screen.getByLabelText('Sending...')).toBeInTheDocument()
  })

  it('shows failed indicator when sendState is "failed"', () => {
    const message: Message = {
      type: 'user',
      content: 'Hello',
      index: 0,
      sendState: 'failed',
      clientMsgId: 'uuid-1',
    }
    renderMessage(message)
    expect(screen.getByLabelText('Send failed')).toBeInTheDocument()
  })

  it('shows no indicator when sendState is undefined', () => {
    const message: Message = {
      type: 'user',
      content: 'Hello',
      index: 0,
      sendState: undefined,
      clientMsgId: 'uuid-1',
    }
    renderMessage(message)
    expect(screen.queryByLabelText('Sending...')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Sent')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Send failed')).not.toBeInTheDocument()
  })

  it('calls onResend when failed indicator is clicked', () => {
    const onResend = vi.fn()
    const message: Message = {
      type: 'user',
      content: 'Hello',
      index: 0,
      sendState: 'failed',
      clientMsgId: 'uuid-1',
    }
    renderMessage(message, { onResend })
    screen.getByLabelText('Send failed').click()
    expect(onResend).toHaveBeenCalledWith(message)
  })
})
```

- [ ] **Step 3: Add test for ws.send() failure in useWebSocket.test.ts**

After the existing `confirmSend` test, add:

```typescript
it("calls onSendFailed immediately when ws.send() throws", () => {
  const onSendFailed = vi.fn()
  const ws = new MockWebSocket("ws://localhost/ws")
  // Make ws.send() throw to simulate a connection in a bad state
  ws.send = vi.fn(() => { throw new Error("connection lost") })
  Object.defineProperty(ws, "readyState", { value: WebSocket.OPEN, writable: true })
  const { result } = renderHook(() =>
    useWebSocket({ userId: "user-1", onMessage: vi.fn(), onSendFailed })
  )
  let clientMsgId = ""
  act(() => {
    clientMsgId = result.current.sendMessage({ message: "test", client_msg_id: "id-throw" }).clientMsgId
  })
  // Timeout should NOT have fired — ws.send() rejection is synchronous
  expect(onSendFailed).toHaveBeenCalledWith("id-throw")
})
```

- [ ] **Step 4: Run tests to verify**

```bash
cd frontend && npm test -- --run
```

Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.test.ts frontend/src/components/MessageBubble.test.tsx frontend/src/hooks/useWebSocket.test.ts
git commit -m "test: update tests for send state simplification"
```

---

### Task 7: Type check and final verification

- [ ] **Step 1: Run TypeScript type check**

```bash
cd frontend && npx tsc --noEmit
```

Expected: No errors.

- [ ] **Step 2: Commit any remaining cleanup**

If type check reveals issues, fix and commit. Otherwise, no commit needed.

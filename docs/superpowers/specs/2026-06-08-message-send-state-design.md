# Message Send State Design

**Date**: 2026-06-08
**Status**: Draft

## Context

Messages go through a send lifecycle: user types, sends, backend confirms. The current implementation has three states (`sending`, `sent`, `failed`). The `sent` state adds no value — once confirmed, the indicator should simply disappear. Failure detection currently relies solely on a 5-minute timeout, missing precise signals from the WebSocket layer.

## Goal

Simplify to two states with precise failure detection:

- `sending` — indicator visible while awaiting backend confirmation
- `failed` — clickable retry on failure
- On confirmation → indicator disappears (no `sent` state)

## State Model

```
undefined ──(user sends)──▶ sending ──(backend confirms)──▶ undefined
                │
                ├── ws.send() throws ──▶ failed (immediate)
                ├── WS disconnect, unconfirmed ──▶ failed (immediate)
                └── timeout ──▶ failed (last-resort fallback)

failed ──(user clicks)──▶ sending (retry)
```

## Failure Detection

Three layers, from most precise to fallback:

1. **`ws.send()` synchronous throw** — message definitely did not leave the client. Mark `failed` immediately.
2. **WebSocket close while unconfirmed** — connection died and the message is unacknowledged. When reconnect attempts are exhausted, mark all unconfirmed messages as `failed`.
3. **Send timeout** — catches any edge case the above two miss. Keep existing `SEND_TIMEOUT_MS` (300s) as fallback.

## Changes

| File | Change |
|------|--------|
| `frontend/src/lib/types.ts` | Remove `'sent'` from `MessageSendState` |
| `frontend/src/components/MessageBubble.tsx` | Remove `sent` → checkmark rendering; confirmed messages show no indicator |
| `frontend/src/hooks/useWebSocket.ts` | Wrap `ws.send()` in try/catch — on throw, fire `onSendFailed` immediately. On connection death (max reconnect attempts), fire new `onConnectionFailed` callback with list of unconfirmed clientMsgIds. |
| `frontend/src/App.tsx` | Confirmation: set `sendState` to `undefined` instead of `'sent'`. Add `onConnectionFailed` handler to bulk-fail unconfirmed messages. |

## Tracking Unconfirmed Messages

`sendStateMapRef` (`Map<clientMsgId, MessageSendState>`) already exists. On WebSocket death, iterate entries — any with value `'sending'` get updated to `'failed'` via `updateSendState`.

## Retry

Unchanged from current behavior. User clicks the failed icon (`✗`), which triggers `handleResend`:
- Generates new `clientMsgId`
- Resets `sendState` to `'sending'`
- Re-sends original content and files

## i18n

Remove `"Sent"` key usage. Keep `"Sending..."`, `"Send failed"`, `"Send timed out"`.

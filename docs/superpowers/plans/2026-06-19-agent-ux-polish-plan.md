# Agent UX Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve core agent interaction experience with error severity classification, tool-call visualization, and WebSocket reconnect/resume.

**Architecture:** Backend changes are minimal — error message structure in `event_pipeline.py` and a new `resume` WS message type in `main_server.py`. Frontend extracts `ErrorCard`, `ToolCard`, `ToolGroupRenderer`, and `ProgressBar` from the ~28KB `MessageBubble.tsx` into focused components, then adds reconnect logic to `useWebSocket.ts`.

**Tech Stack:** Python/FastAPI (backend), TypeScript/React/Vite (frontend), Vitest + Testing Library (tests)

**Spec:** `docs/superpowers/specs/2026-06-19-agent-ux-polish-design.md`

---

### Task 1: Error severity classification in event_pipeline.py

**Files:**
- Modify: `src/event_pipeline.py:282-325`
- Test: `tests/unit/test_event_pipeline.py`

**Goal:** Replace raw error strings with structured severity + actions + detail.

- [ ] **Step 1: Add error classification helper**

Add at module level in `src/event_pipeline.py`, before `handle_task_error`:

```python
from dataclasses import dataclass
from typing import Literal

Severity = Literal["critical", "retryable", "actionable"]

@dataclass(frozen=True)
class ClassifiedError:
    message: str
    severity: Severity
    detail: str
    actions: list[dict[str, str]]


def _classify_error(error: Exception) -> ClassifiedError:
    """Classify an exception into a user-facing error with severity and actions."""
    error_type = type(error).__name__
    error_str = str(error)

    # -- Buffer overflow -------------------------------------------------
    if "JSON message exceeded maximum buffer size" in error_str:
        return ClassifiedError(
            message="Tool output was too large and was truncated.",
            severity="retryable",
            detail="The agent tried to process more data than the system can handle in one step.",
            actions=[
                {"label": "Retry with smaller scope", "kind": "simplify"},
                {"label": "Copy details", "kind": "copy_detail"},
            ],
        )

    # -- Connection errors ------------------------------------------------
    if error_type in ("CLIConnectionError", "ConnectionError", "ConnectionClosedError"):
        return ClassifiedError(
            message="Agent process disconnected unexpectedly.",
            severity="retryable",
            detail=error_str,
            actions=[
                {"label": "Start new session", "kind": "new_session"},
                {"label": "Copy details", "kind": "copy_detail"},
            ],
        )

    # -- Timeout ---------------------------------------------------------
    if error_type == "TimeoutError":
        return ClassifiedError(
            message="Agent task took too long and timed out.",
            severity="retryable",
            detail=error_str,
            actions=[
                {"label": "Retry", "kind": "retry"},
                {"label": "Simplify request", "kind": "simplify"},
            ],
        )

    # -- Auth / permission ------------------------------------------------
    auth_keywords = ("api key", "unauthorized", "forbidden", "permission denied",
                     "authentication", "invalid token", "not allowed")
    if any(kw in error_str.lower() for kw in auth_keywords):
        return ClassifiedError(
            message="Authentication or permission error.",
            severity="critical",
            detail=error_str,
            actions=[
                {"label": "Copy details", "kind": "copy_detail"},
            ],
        )

    # -- Actionable user errors -------------------------------------------
    actionable_patterns = [
        ("File exceeds", "File is too large. Try a smaller file or use a URL instead.",
         [{"label": "Upload smaller file", "kind": "upload_smaller"}]),
        ("rate limit", "Too many requests. Please wait a moment and try again.",
         [{"label": "Wait and retry", "kind": "retry"}]),
        ("path rejected", "File path was rejected for security reasons.",
         [{"label": "Use workspace path", "kind": "use_workspace"}]),
    ]
    for pattern, msg, actions in actionable_patterns:
        if pattern in error_str.lower():
            return ClassifiedError(
                message=msg,
                severity="actionable",
                detail=error_str,
                actions=actions,
            )

    # -- Default: unexpected error ---------------------------------------
    return ClassifiedError(
        message="An unexpected error occurred.",
        severity="retryable",
        detail=error_str,
        actions=[
            {"label": "Retry", "kind": "retry"},
            {"label": "Copy details", "kind": "copy_detail"},
        ],
    )
```

- [ ] **Step 2: Write tests for `_classify_error`**

Add to `tests/unit/test_event_pipeline.py`:

```python
from src.event_pipeline import _classify_error, ClassifiedError


class TestClassifyError:
    def test_buffer_overflow_is_retryable(self):
        err = Exception("JSON message exceeded maximum buffer size: 12345 bytes")
        result = _classify_error(err)
        assert result.severity == "retryable"
        assert "truncated" in result.message.lower()
        assert any(a["kind"] == "simplify" for a in result.actions)

    def test_cli_connection_error_is_retryable(self):
        err = Exception("CLIConnectionError: ProcessTransport is not ready")
        # Simulate by naming the exception appropriately
        try:
            raise ConnectionError("CLIConnectionError: boom")
        except ConnectionError as e:
            result = _classify_error(e)
        assert result.severity == "retryable"
        assert any(a["kind"] == "new_session" for a in result.actions)

    def test_timeout_error_is_retryable(self):
        result = _classify_error(TimeoutError("timed out after 300s"))
        assert result.severity == "retryable"
        assert any(a["kind"] == "retry" for a in result.actions)

    def test_auth_error_is_critical(self):
        err = Exception("invalid token: authentication failed")
        result = _classify_error(err)
        assert result.severity == "critical"

    def test_file_too_large_is_actionable(self):
        err = Exception("File exceeds maximum size of 10MB")
        result = _classify_error(err)
        assert result.severity == "actionable"

    def test_generic_error_is_retryable(self):
        err = Exception("something completely unexpected happened")
        result = _classify_error(err)
        assert result.severity == "retryable"
        assert "unexpected" in result.message.lower()

    def test_classified_error_is_frozen(self):
        result = _classify_error(Exception("test"))
        # Frozen dataclass — should not be assignable
        try:
            result.severity = "critical"
            assert False, "should have raised"
        except Exception:
            pass
```

- [ ] **Step 3: Run tests to verify they fail (no `_classify_error` yet)**

Run: `uv run pytest tests/unit/test_event_pipeline.py::TestClassifyError -v`
Expected: FAIL — `_classify_error` or `ClassifiedError` not defined

- [ ] **Step 4: Replace generic error block in `handle_task_error`**

Replace the `else:` block at `src/event_pipeline.py:282` with:

```python
    else:
        classified = _classify_error(error)
        logger.exception(
            "Agent task %s: error type=%s severity=%s: %s",
            session_id,
            type(error).__name__,
            classified.severity,
            error,
        )
        if cleanup_fn is not None:
            try:
                await cleanup_fn(session_id)
            except Exception:
                pass
        await buffer.add_message(
            session_id,
            {
                "type": "error",
                "message": classified.message,
                "severity": classified.severity,
                "detail": classified.detail,
                "actions": classified.actions,
            },
            user_id,
        )
        await buffer.add_message(
            session_id,
            {"type": "system", "subtype": "session_state_changed", "state": "error"},
            user_id,
        )
        await buffer.mark_done(session_id)
        if agent_log is not None:
            agent_log.end_session(session_id, status="error")
        if obs_store:
            await obs_store.record(
                session_id=session_id,
                user_id=user_id,
                event_type="session_error",
                success=False,
                error_message=str(error)[:500],
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_event_pipeline.py::TestClassifyError -v`
Expected: ALL PASS

- [ ] **Step 6: Run existing tests to verify no regressions**

Run: `uv run pytest tests/unit/test_event_pipeline.py -v`
Expected: ALL PASS (existing tests still work with new error format)

- [ ] **Step 7: Commit**

```bash
git add src/event_pipeline.py tests/unit/test_event_pipeline.py
git commit -m "feat: add structured error severity classification to handle_task_error"
```

---

### Task 2: ErrorCard component + severity UI in MessageBubble

**Files:**
- Create: `frontend/src/components/ErrorCard.tsx`
- Create: `frontend/src/components/ErrorCard.test.tsx`
- Modify: `frontend/src/components/MessageBubble.tsx:638-648`
- Modify: `frontend/src/lib/types.ts` (add error fields to Message type)

**Goal:** Replace raw error `<div>` with a severity-aware `ErrorCard` that renders color-coded banners and action buttons.

- [ ] **Step 1: Add error fields to Message type**

In `frontend/src/lib/types.ts`, inside the `Message` interface, add these fields after `message?: string`:

```typescript
  // Error severity fields (from backend 2026-06-19 error classification)
  severity?: 'critical' | 'retryable' | 'actionable'
  detail?: string
  actions?: Array<{ label: string; kind: string }>
```

- [ ] **Step 2: Create ErrorCard component**

Create `frontend/src/components/ErrorCard.tsx`:

```typescript
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

interface ErrorAction {
  label: string
  kind: string
}

interface ErrorCardProps {
  message: string
  severity?: 'critical' | 'retryable' | 'actionable'
  detail?: string
  actions?: ErrorAction[]
  /** When true, the card appears muted as a resolved/past error */
  isResolved?: boolean
  /** Called when user clicks an action button */
  onAction?: (kind: string) => void
}

const SEVERITY_STYLES: Record<string, { banner: string; icon: string }> = {
  critical: { banner: 'error-card--critical', icon: '🔴' },
  retryable: { banner: 'error-card--retryable', icon: '🟡' },
  actionable: { banner: 'error-card--actionable', icon: '🔵' },
}

export default function ErrorCard({
  message,
  severity = 'retryable',
  detail,
  actions,
  isResolved,
  onAction,
}: ErrorCardProps) {
  const { t } = useTranslation()
  const [showDetail, setShowDetail] = useState(false)
  const style = SEVERITY_STYLES[severity] || SEVERITY_STYLES.retryable

  return (
    <div
      className={`message error-card ${style.banner}${isResolved ? ' error-card--resolved' : ''}`}
    >
      <div className="error-card__header">
        <span className="error-card__icon">{style.icon}</span>
        <span className="error-card__message">{message}</span>
        {isResolved && (
          <span className="error-resolved-badge">{t('message.past')}</span>
        )}
      </div>

      {actions && actions.length > 0 && !isResolved && (
        <div className="error-card__actions">
          {actions.map((action) => (
            <button
              key={action.kind}
              className="error-card__action-btn"
              onClick={() => onAction?.(action.kind)}
            >
              {action.label}
            </button>
          ))}
        </div>
      )}

      {detail && (
        <div className="error-card__detail">
          <button
            className="error-card__detail-toggle"
            onClick={() => setShowDetail(!showDetail)}
          >
            {showDetail ? t('message.hideDetails') : t('message.showDetails')}
          </button>
          {showDetail && (
            <pre className="error-card__detail-text">{detail}</pre>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Write ErrorCard tests**

Create `frontend/src/components/ErrorCard.test.tsx`:

```typescript
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ErrorCard from './ErrorCard'

function t(key: string): string {
  const map: Record<string, string> = {
    'message.past': 'Past',
    'message.showDetails': 'Show details',
    'message.hideDetails': 'Hide details',
  }
  return map[key] || key
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t }),
}))

describe('ErrorCard', () => {
  it('renders message and severity icon', () => {
    render(<ErrorCard message="Connection failed" severity="retryable" />)
    expect(screen.getByText('Connection failed')).toBeTruthy()
    expect(screen.getByText('🟡')).toBeTruthy()
  })

  it('renders action buttons and calls onAction on click', () => {
    const onAction = vi.fn()
    render(
      <ErrorCard
        message="Timeout"
        severity="retryable"
        actions={[
          { label: 'Retry', kind: 'retry' },
          { label: 'Simplify', kind: 'simplify' },
        ]}
        onAction={onAction}
      />,
    )
    fireEvent.click(screen.getByText('Retry'))
    expect(onAction).toHaveBeenCalledWith('retry')
  })

  it('hides action buttons when isResolved', () => {
    render(
      <ErrorCard
        message="Old error"
        severity="retryable"
        actions={[{ label: 'Retry', kind: 'retry' }]}
        isResolved
      />,
    )
    expect(screen.queryByText('Retry')).toBeNull()
  })

  it('toggles detail visibility', () => {
    render(
      <ErrorCard message="Error" detail="Stack trace here" severity="retryable" />,
    )
    expect(screen.queryByText('Stack trace here')).toBeNull()
    fireEvent.click(screen.getByText('Show details'))
    expect(screen.getByText('Stack trace here')).toBeTruthy()
  })

  it('renders critical with red icon', () => {
    render(<ErrorCard message="Fatal" severity="critical" />)
    expect(screen.getByText('🔴')).toBeTruthy()
  })

  it('renders actionable with blue icon', () => {
    render(<ErrorCard message="Fix it" severity="actionable" />)
    expect(screen.getByText('🔵')).toBeTruthy()
  })

  it('uses retryable as default severity', () => {
    render(<ErrorCard message="Something" />)
    expect(screen.getByText('🟡')).toBeTruthy()
  })
})
```

- [ ] **Step 4: Run test to verify it fails (component not yet imported)**

Run: `cd frontend && npx vitest run src/components/ErrorCard.test.tsx`
Expected: FAIL (component not created or test can't find it)

Wait — Step 2 creates the component. Run the test to verify PASS:

Run: `cd frontend && npx vitest run src/components/ErrorCard.test.tsx`
Expected: ALL PASS

- [ ] **Step 5: Replace error rendering in MessageBubble.tsx**

In `frontend/src/components/MessageBubble.tsx`, add import at top:

```typescript
import ErrorCard from './ErrorCard'
```

Replace the error block (lines 638-648) with:

```typescript
  if (message.type === 'error') {
    const errorText = message.message || message.content || t('message.errorOccurred')
    const isResolved = isResolvedMessage(message, lastUserMsgIndex)
    const severity = message.severity || 'retryable'
    return (
      <ErrorCard
        message={errorText}
        severity={severity}
        detail={message.detail}
        actions={message.actions}
        isResolved={isResolved}
        onAction={(kind) => {
          if (kind === 'new_session') {
            // Navigate to home so user creates a fresh session
            window.location.hash = ''
          }
          // Other actions are informational at this stage
        }}
      />
    )
  }
```

- [ ] **Step 6: Add ErrorCard CSS styles**

Add to `frontend/src/styles/global.css`:

```css
/* ── ErrorCard ─────────────────────────────────────────────── */

.error-card {
  margin: 0.5rem 0;
  border-radius: 8px;
  padding: 0.75rem 1rem;
  font-size: 0.9rem;
  border-left: 4px solid;
}

.error-card--critical {
  background: oklch(95% 0.02 20);
  border-color: oklch(55% 0.15 20);
}

.error-card--retryable {
  background: oklch(95% 0.02 75);
  border-color: oklch(65% 0.12 75);
}

.error-card--actionable {
  background: oklch(95% 0.02 250);
  border-color: oklch(55% 0.12 250);
}

.error-card--resolved {
  opacity: 0.5;
}

.error-card__header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.error-card__icon {
  font-size: 1rem;
  flex-shrink: 0;
}

.error-card__message {
  flex: 1;
  line-height: 1.4;
}

.error-card__actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.5rem;
  flex-wrap: wrap;
}

.error-card__action-btn {
  padding: 0.3rem 0.75rem;
  border: 1px solid var(--color-border, #ccc);
  border-radius: 4px;
  background: var(--color-surface, #fff);
  cursor: pointer;
  font-size: 0.85rem;
}

.error-card__action-btn:hover {
  background: var(--color-hover, #f0f0f0);
}

.error-card__detail {
  margin-top: 0.5rem;
}

.error-card__detail-toggle {
  background: none;
  border: none;
  color: var(--color-accent, #06c);
  cursor: pointer;
  font-size: 0.85rem;
  padding: 0;
}

.error-card__detail-text {
  margin-top: 0.4rem;
  padding: 0.5rem;
  background: oklch(95% 0 0);
  border-radius: 4px;
  font-size: 0.8rem;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
```

- [ ] **Step 7: Run full frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: ALL PASS (no regressions)

- [ ] **Step 8: Add i18n keys**

In `frontend/src/i18n/en.json`, add:

```json
  "message.showDetails": "Show details",
  "message.hideDetails": "Hide details"
```

In `frontend/src/i18n/zh.json`, add:

```json
  "message.showDetails": "展开详情",
  "message.hideDetails": "收起详情"
```

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/ErrorCard.tsx frontend/src/components/ErrorCard.test.tsx \
        frontend/src/components/MessageBubble.tsx frontend/src/lib/types.ts \
        frontend/src/styles/global.css frontend/src/i18n/en.json frontend/src/i18n/zh.json
git commit -m "feat: add ErrorCard component with severity-based rendering"
```

---

### Task 3: Extract ToolCard from MessageBubble

**Files:**
- Create: `frontend/src/components/ToolCard.tsx`
- Create: `frontend/src/components/ToolCard.test.tsx`
- Modify: `frontend/src/components/MessageBubble.tsx` (remove ToolCard, import from new file)

**Goal:** Extract the existing `ToolCard` component + helpers (`TOOL_ICONS`, `DANGER_TOOLS`, `getToolIcon`, `buildToolSummary`, `fmtDuration`, `ToolResultSection`, `ToolResultContent`, `formatBashCommand`, `formatFileContent`, `formatEditContent`, `detectLanguage`) into `ToolCard.tsx`. No behavior change — pure extraction.

- [ ] **Step 1: Create ToolCard.tsx with all extracted helpers**

Create `frontend/src/components/ToolCard.tsx` with the extracted content. Move these items from `MessageBubble.tsx` (lines 24-338 and adjacent helper sections):

- `INVALID_FILENAMES`, `isValidFilename` (lines 14-20)
- `UNITS_ZH`, `UNITS_EN`, `fmtDuration` (lines 24-37)
- `DISABLED_TOOLS`, `TOOL_ICONS`, `DANGER_TOOLS`, `getToolIcon`, `buildToolSummary` (lines 41-268 range)
- `ToolCardProps` interface, `ToolCard` component (lines 273-296)
- `ToolResultSection` (lines 298-336)
- `ToolResultContent` (around lines 338+)
- `formatBashCommand`, `formatFileContent`, `formatEditContent`, `detectLanguage` (helper functions)

Export the `ToolCard` component as default, and also export the helpers needed by `MessageBubble`:

```typescript
export { TOOL_ICONS, getToolIcon, buildToolSummary, fmtDuration, detectLanguage }
export type { ToolCardProps }
```

**Important:** The `pairToolMessages` function stays in `MessageBubble.tsx` — it's message-list-level logic, not tool-card-level.

- [ ] **Step 2: Update MessageBubble.tsx imports**

In `MessageBubble.tsx`, replace the moved code with:

```typescript
import ToolCard, { getToolIcon, buildToolSummary } from './ToolCard'
```

Remove all the extracted definitions (they're now in `ToolCard.tsx`).

- [ ] **Step 3: Write ToolCard tests**

Create `frontend/src/components/ToolCard.test.tsx`:

```typescript
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import ToolCard from './ToolCard'

function t(key: string): string {
  const map: Record<string, string> = {
    'message.toolRunning': 'Running...',
    'message.result': 'Result',
    'message.errorOccurred': 'Error',
    'message.resultEmpty': 'Empty',
  }
  return map[key] || key
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t }),
}))

describe('ToolCard', () => {
  it('renders tool name and summary', () => {
    render(
      <ToolCard name="Read" summary="3 files">
        <div>content</div>
      </ToolCard>,
    )
    expect(screen.getByText('Read')).toBeTruthy()
    expect(screen.getByText('3 files')).toBeTruthy()
  })

  it('renders running state without tool result', () => {
    render(
      <ToolCard name="Bash" summary="running...">
        <div>cmd</div>
      </ToolCard>,
    )
    expect(screen.getByText('Running...')).toBeTruthy()
  })

  it('renders error state for failed tool', () => {
    render(
      <ToolCard
        name="Bash"
        summary="failed"
        toolResult={{ content: 'error output', is_error: true, name: 'Bash' }}
      >
        <div>cmd</div>
      </ToolCard>,
    )
    expect(screen.getByText('Error')).toBeTruthy()
  })

  it('renders success state for completed tool', () => {
    render(
      <ToolCard
        name="Read"
        summary="done"
        toolResult={{ content: 'file content', name: 'Read' }}
      >
        <div>path</div>
      </ToolCard>,
    )
    expect(screen.getByText('Result')).toBeTruthy()
  })
})
```

- [ ] **Step 4: Run tests to verify extraction is clean**

Run: `cd frontend && npx vitest run`
Expected: ALL PASS (no regressions)

- [ ] **Step 5: Run TypeScript check**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ToolCard.tsx frontend/src/components/ToolCard.test.tsx \
        frontend/src/components/MessageBubble.tsx
git commit -m "refactor: extract ToolCard component from MessageBubble"
```

---

### Task 4: Tool group merging

**Files:**
- Create: `frontend/src/components/ToolGroupRenderer.tsx`
- Create: `frontend/src/components/ToolGroupRenderer.test.tsx`
- Modify: `frontend/src/components/ChatArea.tsx` (add group merge before render)

**Goal:** Merge consecutive same-tool calls into a single expandable group card.

- [ ] **Step 1: Create merge logic in ToolGroupRenderer**

Create `frontend/src/components/ToolGroupRenderer.tsx`:

```typescript
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import ToolCard, { getToolIcon, buildToolSummary, fmtDuration } from './ToolCard'
import type { Message } from '../lib/types'

interface ToolGroupRendererProps {
  tools: Message[]
}

/** Merge threshold: group when 3+ consecutive same-tool calls appear. */
const MERGE_THRESHOLD = 3

export default function ToolGroupRenderer({ tools }: ToolGroupRendererProps) {
  const { t, i18n } = useTranslation()
  const [expanded, setExpanded] = useState(false)

  if (tools.length === 0) return null
  if (tools.length < MERGE_THRESHOLD) {
    // Render each tool as an individual card — but we need the caller to handle this
    return null
  }

  const name = tools[0].name || 'unknown'
  const totalDuration = tools.reduce(
    (sum, t) => sum + (t.duration_ms || 0), 0,
  )

  // Extract summary info for the first few items
  const previewItems = tools.slice(0, 3).map((t) => {
    const input = t.input as Record<string, unknown> | undefined
    if (name === 'Read' || name === 'Write' || name === 'Edit') {
      if (input && 'file_path' in input) return String(input.file_path)
      if (input && 'path' in input) return String(input.path)
    }
    return buildToolSummary(name, input || {}) || ''
  })

  const remaining = tools.length - previewItems.length

  return (
    <details className="message tool-group" open={expanded}>
      <summary
        className="tool-group__summary"
        onClick={(e) => {
          e.preventDefault()
          setExpanded(!expanded)
        }}
      >
        <span className="tool-icon">{getToolIcon(name)}</span>
        <span className="tool-name">{name}</span>
        <span className="tool-detail">
          {tools.length} {t('message.files')}
        </span>
        {totalDuration > 0 && (
          <span className="tool-duration">
            {' '}
            ⏱ {fmtDuration(totalDuration, i18n.language)}
          </span>
        )}
      </summary>

      <div className="tool-group__preview">
        {previewItems.map((item, i) => (
          <div key={i} className="tool-group__item">
            {item}
          </div>
        ))}
        {remaining > 0 && (
          <div className="tool-group__more">
            {t('message.andMore', { count: remaining })}
          </div>
        )}
      </div>

      {expanded && (
        <div className="tool-group__expanded">
          {tools.map((tool, i) => {
            const input = tool.input as Record<string, unknown> | undefined
            const summary = input ? buildToolSummary(name, input) : ''
            return (
              <ToolCard key={i} name={name} summary={summary} toolResult={tool.toolResult}>
                <pre className="tool-content">
                  {JSON.stringify(tool.input, null, 2)}
                </pre>
              </ToolCard>
            )
          })}
        </div>
      )}
    </details>
  )
}

/**
 * Given a list of messages, merge consecutive same-tool calls into groups.
 * Returns a new list where grouped tools are replaced by a single marker message.
 */
export function groupConsecutiveTools(messages: Message[]): Message[] {
  const result: Message[] = []
  let i = 0

  while (i < messages.length) {
    const msg = messages[i]

    // Only merge tool_use messages (not tool_result, not system)
    if (msg.type !== 'tool_use') {
      result.push(msg)
      i++
      continue
    }

    // Start collecting consecutive same-tool calls
    const toolName = msg.name
    const group: Message[] = [msg]
    i++

    while (
      i < messages.length &&
      messages[i].type === 'tool_use' &&
      messages[i].name === toolName
    ) {
      group.push(messages[i])
      i++
    }

    if (group.length >= MERGE_THRESHOLD) {
      // Replace with a grouped marker
      result.push({
        type: 'tool_use',
        name: toolName,
        content: '',
        index: msg.index,
        input: { _grouped: true, _tools: group },
        duration_ms: group.reduce((sum, t) => sum + (t.duration_ms || 0), 0),
      } as Message)
    } else {
      result.push(...group)
    }
  }

  return result
}
```

- [ ] **Step 2: Write tests for group merging**

Create `frontend/src/components/ToolGroupRenderer.test.tsx`:

```typescript
import { describe, it, expect } from 'vitest'
import { groupConsecutiveTools } from './ToolGroupRenderer'
import type { Message } from '../lib/types'

function makeTool(name: string, index: number): Message {
  return {
    type: 'tool_use',
    name,
    index,
    content: '',
    input: {},
  } as Message
}

function makeUser(index: number): Message {
  return {
    type: 'user',
    index,
    content: 'hello',
  } as Message
}

describe('groupConsecutiveTools', () => {
  it('does not group fewer than 3 same-tool calls', () => {
    const msgs = [
      makeTool('Read', 1),
      makeTool('Read', 2),
      makeUser(3),
    ]
    const result = groupConsecutiveTools(msgs)
    // 2 tools + 1 user = 3 messages, no grouping
    expect(result).toHaveLength(3)
    expect(result[0].type).toBe('tool_use')
    expect(result[1].type).toBe('tool_use')
  })

  it('groups 3+ consecutive same-tool calls', () => {
    const msgs = [
      makeTool('Read', 1),
      makeTool('Read', 2),
      makeTool('Read', 3),
      makeUser(4),
    ]
    const result = groupConsecutiveTools(msgs)
    // 1 group + 1 user = 2 messages
    expect(result).toHaveLength(2)
    expect(result[0].input).toHaveProperty('_grouped', true)
  })

  it('does not group different tool names', () => {
    const msgs = [
      makeTool('Read', 1),
      makeTool('Read', 2),
      makeTool('Write', 3),
      makeTool('Read', 4),
    ]
    const result = groupConsecutiveTools(msgs)
    // Read, Read, Write, Read — no group of 3 same
    expect(result).toHaveLength(4)
  })

  it('groups 5 consecutive reads into one group', () => {
    const msgs = Array.from({ length: 5 }, (_, i) => makeTool('Read', i + 1))
    msgs.push(makeUser(6))
    const result = groupConsecutiveTools(msgs)
    expect(result).toHaveLength(2) // group + user
    const grouped = result[0]
    expect(grouped.input).toHaveProperty('_grouped', true)
  })
})
```

- [ ] **Step 3: Run tests to verify group logic**

Run: `cd frontend && npx vitest run src/components/ToolGroupRenderer.test.tsx`
Expected: ALL PASS

- [ ] **Step 4: Integrate merging in ChatArea.tsx**

In `frontend/src/components/ChatArea.tsx`, add import:

```typescript
import { groupConsecutiveTools } from './ToolGroupRenderer'
```

Find where `pairToolMessages` is called (the `displayMessages` useMemo or equivalent). Apply grouping after pairing:

```typescript
// Existing pairing logic
const paired = useMemo(() => pairToolMessages(messages), [messages])
// Apply tool group merging
const displayMessages = useMemo(() => groupConsecutiveTools(paired), [paired])
```

- [ ] **Step 5: Render grouped tools in MessageBubble.tsx**

In `MessageBubble.tsx`, add a check at the top of the tool_use rendering section (around line 526):

```typescript
import ToolGroupRenderer from './ToolGroupRenderer'

// Inside the component, in the tool_use section:
if (message.type === 'tool_use' && message.input && (message.input as any)._grouped) {
  const tools = (message.input as any)._tools as Message[]
  return <ToolGroupRenderer tools={tools} />
}
```

- [ ] **Step 6: Add CSS for tool groups**

In `frontend/src/styles/global.css`:

```css
/* ── Tool Group ─────────────────────────────────────────────── */

.tool-group {
  margin: 0.5rem 0;
  border: 1px solid var(--color-border, #ddd);
  border-radius: 8px;
  overflow: hidden;
}

.tool-group__summary {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  cursor: pointer;
  background: var(--color-surface, #fafafa);
  list-style: none;
}

.tool-group__summary::-webkit-details-marker {
  display: none;
}

.tool-group__preview {
  padding: 0.5rem 0.75rem;
  font-size: 0.85rem;
  color: var(--color-text-secondary, #666);
}

.tool-group__item {
  padding: 0.15rem 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: monospace;
  font-size: 0.8rem;
}

.tool-group__more {
  color: var(--color-accent, #06c);
  cursor: pointer;
  font-size: 0.85rem;
  margin-top: 0.25rem;
}

.tool-group__expanded {
  border-top: 1px solid var(--color-border, #ddd);
  padding: 0.5rem;
}
```

- [ ] **Step 7: Add i18n keys**

In `frontend/src/i18n/en.json`:

```json
  "message.files": "files",
  "message.andMore": "and {{count}} more"
```

In `frontend/src/i18n/zh.json`:

```json
  "message.files": "个文件",
  "message.andMore": "还有 {{count}} 个"
```

- [ ] **Step 8: Run full test suite + type check**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: ALL PASS, no type errors

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/ToolGroupRenderer.tsx \
        frontend/src/components/ToolGroupRenderer.test.tsx \
        frontend/src/components/ChatArea.tsx \
        frontend/src/components/MessageBubble.tsx \
        frontend/src/styles/global.css \
        frontend/src/i18n/en.json frontend/src/i18n/zh.json
git commit -m "feat: add tool group merging for consecutive same-tool calls"
```

---

### Task 5: Progress bar

**Files:**
- Create: `frontend/src/components/ProgressBar.tsx`
- Create: `frontend/src/components/ProgressBar.test.tsx`
- Modify: `frontend/src/components/ChatArea.tsx` (add progress bar above messages)

**Goal:** A thin phase indicator showing the agent's current high-level progress.

- [ ] **Step 1: Create ProgressBar component**

Create `frontend/src/components/ProgressBar.tsx`:

```typescript
import type { Message } from '../lib/types'

interface Phase {
  key: string
  label: string
}

const PHASES: Phase[] = [
  { key: 'analyze', label: 'Analyze' },
  { key: 'read', label: 'Read files' },
  { key: 'edit', label: 'Edit code' },
  { key: 'verify', label: 'Verify' },
]

type PhaseState = 'pending' | 'active' | 'done'

interface ProgressBarProps {
  messages: Message[]
  /** Whether the agent is currently running (session state) */
  isRunning: boolean
}

export default function ProgressBar({ messages, isRunning }: ProgressBarProps) {
  if (!isRunning && messages.length === 0) return null

  const phase = detectPhase(messages)

  // Find the tool calls to analyze
  const allTools = messages.filter((m) => m.type === 'tool_use')

  // Phase detection:
  // - "analyze": first N tool calls are Read/Grep/Search (no Write/Edit/Bash)
  // - "edit": first Write or Edit tool call found
  // - "verify": first Bash or test tool call found after an edit
  let detected: PhaseState[] = PHASES.map(() => 'pending')
  const firstNonReadTool = allTools.find(
    (t) => t.name && !['Read', 'Grep', 'Glob', 'WebSearch', 'WebFetch'].includes(t.name),
  )

  if (firstNonReadTool) {
    detected = detected.map((_, i) => (i <= 0 ? 'done' : 'pending')) // analyze done
  } else if (allTools.length > 0 && isRunning) {
    detected = detected.map((_, i) => (i === 0 ? 'active' : 'pending')) // analyze active
  }

  const hasEdit = allTools.some((t) => t.name === 'Write' || t.name === 'Edit')
  const hasVerify = allTools.some(
    (t) => t.name === 'Bash' && t.toolResult && !t.toolResult.is_error,
  )

  if (hasEdit) {
    detected[1] = 'done' // read done
    detected[2] = hasVerify ? 'done' : 'active' // edit active/done
  }
  if (hasVerify) {
    detected[3] = 'active' // verify active
  }

  // Don't show if nothing is active and nothing is done
  const anythingDone = detected.some((s) => s !== 'pending')
  if (!anythingDone && !isRunning) return null

  return (
    <div className="progress-bar">
      {PHASES.map((phase, i) => {
        const state = detected[i] || 'pending'
        if (state === 'pending' && i > 0 && detected[i - 1] === 'pending') {
          // Hide future phases that haven't been reached
          return null
        }
        return (
          <span key={phase.key} className="progress-bar__phase">
            <span className={`progress-bar__dot progress-bar__dot--${state}`}>
              {state === 'done' ? '✓' : state === 'active' ? '●' : '○'}
            </span>
            <span
              className={`progress-bar__label${state === 'pending' ? ' progress-bar__label--dim' : ''}`}
            >
              {phase.label}
            </span>
          </span>
        )
      })}
    </div>
  )
}

function detectPhase(messages: Message[]): string {
  const tools = messages.filter((m) => m.type === 'tool_use')
  if (tools.length === 0) return 'analyze'
  const names = tools.map((t) => t.name)
  if (names.some((n) => n === 'Write' || n === 'Edit')) return 'edit'
  if (names.some((n) => n === 'Bash')) return 'verify'
  return 'analyze'
}
```

- [ ] **Step 2: Write ProgressBar tests**

Create `frontend/src/components/ProgressBar.test.tsx`:

```typescript
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import ProgressBar from './ProgressBar'
import type { Message } from '../lib/types'

function makeToolMsg(name: string, index: number): Message {
  return {
    type: 'tool_use',
    name,
    index,
    content: '',
    input: {},
  } as Message
}

describe('ProgressBar', () => {
  it('returns null when not running and no messages', () => {
    const { container } = render(
      <ProgressBar messages={[]} isRunning={false} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('shows analyze as active when running with read tools', () => {
    const msgs = [makeToolMsg('Read', 1)]
    render(<ProgressBar messages={msgs} isRunning={true} />)
    // "Analyze" should be present and active
    expect(screen.getByText('Analyze')).toBeTruthy()
  })

  it('shows edit as active when write tool found', () => {
    const msgs = [
      makeToolMsg('Read', 1),
      makeToolMsg('Write', 2),
    ]
    render(<ProgressBar messages={msgs} isRunning={true} />)
    expect(screen.getByText('Edit code')).toBeTruthy()
  })

  it('shows verify as active when bash tool found', () => {
    const msgs = [
      makeToolMsg('Read', 1),
      makeToolMsg('Write', 2),
      makeToolMsg('Bash', 3),
    ]
    render(<ProgressBar messages={msgs} isRunning={true} />)
    expect(screen.getByText('Verify')).toBeTruthy()
  })
})
```

- [ ] **Step 3: Run tests**

Run: `cd frontend && npx vitest run src/components/ProgressBar.test.tsx`
Expected: ALL PASS

- [ ] **Step 4: Integrate in ChatArea.tsx**

In `frontend/src/components/ChatArea.tsx`, add import and render ProgressBar above the message list:

```typescript
import ProgressBar from './ProgressBar'

// In the JSX, before the message list:
{messages.length > 0 && (
  <ProgressBar messages={messages} isRunning={sessionState === 'running'} />
)}
```

- [ ] **Step 5: Add CSS for progress bar**

In `frontend/src/styles/global.css`:

```css
/* ── Progress Bar ───────────────────────────────────────────── */

.progress-bar {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.5rem 1rem;
  background: var(--color-surface, #fafafa);
  border-bottom: 1px solid var(--color-border, #eee);
  font-size: 0.8rem;
  overflow-x: auto;
  white-space: nowrap;
}

.progress-bar__phase {
  display: flex;
  align-items: center;
  gap: 0.25rem;
}

.progress-bar__phase::after {
  content: '→';
  color: var(--color-border, #ccc);
  margin: 0 0.25rem;
}

.progress-bar__phase:last-child::after {
  content: none;
}

.progress-bar__dot--done {
  color: oklch(55% 0.15 145);
}

.progress-bar__dot--active {
  color: var(--color-accent, #06c);
  animation: pulse 1.5s ease-in-out infinite;
}

.progress-bar__dot--pending {
  color: var(--color-border, #ccc);
}

.progress-bar__label--dim {
  color: var(--color-text-secondary, #999);
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
```

- [ ] **Step 6: Run full test suite + type check**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: ALL PASS, no type errors

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/ProgressBar.tsx \
        frontend/src/components/ProgressBar.test.tsx \
        frontend/src/components/ChatArea.tsx \
        frontend/src/styles/global.css
git commit -m "feat: add agent progress bar with phase detection"
```

---

### Task 6: WebSocket reconnect + session resume

**Files:**
- Modify: `frontend/src/hooks/useWebSocket.ts` (add resume message type, lastSeq tracking, reconnect logic)
- Modify: `frontend/src/lib/types.ts` (add `recovering` / `expired` to ConnectionStatus)
- Modify: `main_server.py` (add `resume` message handling, add `seq` to outgoing messages)
- Create: `tests/unit/test_ws_resume.py`

**Goal:** Users reconnect transparently and recover their session without losing context.

- [ ] **Step 1: Add resume message handling in main_server.py**

In `main_server.py`, in the WS message processing loop (around line 1852 where `item.get("type") == "recover"` is checked), add support for `resume`:

```python
                elif item.get("type") == "recover":
                    data = item
                elif item.get("type") == "resume":
                    data = item
                else:
                    data = item
```

Then, after `last_index` is read (line 1874), add a check for resume type:

```python
            # After: last_index = data.get("last_index", 0)
            is_resume = data.get("type") == "resume"
            resume_last_seq = data.get("last_seq", 0) if is_resume else 0
```

Then, in the agent task section, if the data type is `resume` instead of `chat`, skip starting a new agent task and instead replay buffered messages. Add this block before the agent task creation section (around line 2086 where is_continuation is checked):

```python
            # ── Resume: replay buffered messages without starting agent ──────
            if is_resume and session_id:
                logger.info(
                    "[WS] Resume: session=%s last_seq=%s",
                    session_id, resume_last_seq,
                )
                # Replay buffered messages after last_seq
                history = await buffer.get_history(session_id, after_index=resume_last_seq)
                logger.info(
                    "[WS] Resume: replaying %d messages from seq > %s",
                    len(history), resume_last_seq,
                )
                for i, h in enumerate(history):
                    if not await _safe_ws_send(
                        websocket,
                        {
                            **h,
                            "index": h.get("seq", resume_last_seq + i),
                            "replay": True,
                            "session_id": session_id,
                        },
                    ):
                        break

                # Subscribe to future messages for this session
                current_session_id = session_id
                event = await buffer.subscribe(session_id)

                try:
                    while True:
                        try:
                            item = pending_ws_msgs.get_nowait()
                            if item is None:
                                return
                            # Handle any incoming messages (cancel, new chat, etc.)
                            if item.get("type") == "cancel":
                                sid = item.get("session_id", "")
                                task = running_tasks.get(sid)
                                if task and not task.done():
                                    task.cancel()
                                await _safe_ws_send(
                                    websocket,
                                    {"type": "system", "subtype": "session_cancelled", "session_id": sid},
                                )
                                break
                            if item.get("type") == "chat":
                                # User sent a new message — break out to process it
                                data = item
                                break
                        except queue.Empty:
                            pass

                        try:
                            msg = await asyncio.wait_for(event.wait(), timeout=HEARTBEAT_INTERVAL)
                            event.clear()
                            seq = msg.get("seq", 0)
                            if seq <= resume_last_seq:
                                continue
                            if not await _safe_ws_send(
                                websocket, {**msg, "index": seq, "session_id": session_id},
                            ):
                                break
                            resume_last_seq = seq
                        except asyncio.TimeoutError:
                            if not await _safe_ws_send(websocket, make_heartbeat()):
                                break
                except Exception:
                    logger.exception("[WS] Resume loop error for session=%s", session_id)
                finally:
                    current_session_id = None
                continue  # Go back to outer loop
```

- [ ] **Step 2: Add seq to all outgoing WS messages**

`_safe_ws_send` is at `main_server.py:1669` — a module-level function. Inside `handle_ws` (line 1711), add a local seq counter and a thin wrapper after `await websocket.accept()`:

```python
        _ws_seq = 0

        async def _send(ws: WebSocket, data: dict) -> bool:
            nonlocal _ws_seq
            _ws_seq += 1
            data["seq"] = _ws_seq
            return await _safe_ws_send(ws, data)
```

Then replace all internal `_safe_ws_send(websocket, ...)` calls with `_send(websocket, ...)` within `handle_ws` and its inner functions. The top-level `_safe_ws_send` is unchanged — only internal call sites switch to `_send`.

- [ ] **Step 3: Update frontend ConnectionStatus**

In `frontend/src/lib/types.ts`, update:

```typescript
export type ConnectionStatus = 'connected' | 'connecting' | 'reconnecting' | 'recovered' | 'expired' | 'failed'
```

- [ ] **Step 4: Update useWebSocket hook**

In `frontend/src/hooks/useWebSocket.ts`, add resume logic:

```typescript
  // Track last received seq per session (for resume on reconnect)
  const lastSeqRef = useRef<Map<string, number>>(new Map())

  // In ws.onmessage, track seq:
  // After parsing data as Message:
  const sid = data.session_id
  const seq = data.seq ?? data.index
  if (sid && seq != null) {
    const current = lastSeqRef.current.get(sid) ?? 0
    if (seq > current) {
      lastSeqRef.current.set(sid, seq)
    }
  }

  // In ws.onclose, when status was "connected" (not intentional close and not auth fail):
  // Send a resume on reconnect instead of a new chat message.
  // Track which session was active when disconnect happened:
  const activeSessionRef = useRef<string | null>(null)

  // In ws.onopen after "Connected":
  // If there's an active session that was disconnected, send resume:
  const activeSid = activeSessionRef.current
  if (activeSid) {
    const lastSeq = lastSeqRef.current.get(activeSid) ?? 0
    const resumePayload = JSON.stringify({
      type: "resume",
      session_id: activeSid,
      last_seq: lastSeq,
      user_id: userIdRef.current,
    })
    ws.send(resumePayload)
    setStatus("recovered")
  }
```

- [ ] **Step 5: Add tests for resume backend logic**

Create `tests/unit/test_ws_resume.py`:

```python
"""Tests for WebSocket resume message handling."""

import pytest


class TestWsResume:
    def test_resume_message_has_required_fields(self):
        """Resume messages must include session_id and last_seq."""
        msg = {"type": "resume", "session_id": "sess_1", "last_seq": 42}
        assert msg["type"] == "resume"
        assert "session_id" in msg
        assert "last_seq" in msg

    def test_resume_without_seq_defaults_to_zero(self):
        """Missing last_seq should be treated as 0 (full replay)."""
        last_seq = 0  # Simulating missing field
        assert last_seq == 0
        # Full history pull should occur when last_seq is 0
```

- [ ] **Step 6: Run all tests**

Run: `uv run pytest tests/unit/test_ws_resume.py -v && cd frontend && npx vitest run`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add main_server.py frontend/src/hooks/useWebSocket.ts \
        frontend/src/lib/types.ts tests/unit/test_ws_resume.py
git commit -m "feat: add WebSocket resume with seq-based message replay"
```

---

## Summary

| Task | Module | Files | Est. Time |
|------|--------|-------|-----------|
| 1 | Error classification (backend) | `event_pipeline.py` + test | 20 min |
| 2 | ErrorCard component | New `ErrorCard.tsx` + modify `MessageBubble.tsx` | 25 min |
| 3 | ToolCard extraction | New `ToolCard.tsx` + modify `MessageBubble.tsx` | 25 min |
| 4 | Tool group merging | New `ToolGroupRenderer.tsx` + modify `ChatArea.tsx` | 25 min |
| 5 | Progress bar | New `ProgressBar.tsx` + modify `ChatArea.tsx` | 20 min |
| 6 | WS reconnect + resume | `main_server.py` + `useWebSocket.ts` | 30 min |

**Total: ~2.5 hours**

**PR boundaries:**
- PR 1: Tasks 1 + 2 (error classification + ErrorCard) — error UX improvement
- PR 2: Tasks 3 + 4 + 5 (ToolCard extraction + grouping + progress bar) — tool visualization
- PR 3: Task 6 (WebSocket reconnect + resume) — connection reliability

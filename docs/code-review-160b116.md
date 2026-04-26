# Code Review Report

**Commit**: `160b116` — `refactor: refine skill evolution pipeline and remove dead code`
**Branch**: `optimize-code` → `main`
**Reviewed**: 2026-04-26
**Decision**: **REQUEST CHANGES**

---

## Summary

Review covers 29 changed files (+1259/-856). The commit refactors the skill evolution pipeline by lowering thresholds, removing dead code (A/B testing, session cleanup), removing admin guards from evolution endpoints, and adding DB-backed fallbacks. Static review: 7 HIGH, 8 MEDIUM, 4 LOW.

---

## Findings

### CRITICAL

None.

### HIGH — 7 issues

| # | File | Line | Issue |
|---|------|------|-------|
| 1 | `main_server.py` | 1639-1665 | **WebSocket user impersonation** — JWT token is verified but the identity is discarded; `user_id` is read from untrusted WebSocket message body instead of the verified token. Any authenticated user can set an arbitrary `user_id` in their WebSocket payload, effectively impersonating another user for all WebSocket-driven operations (creating sessions, sending answers, reading history). |
| 2 | `main_server.py` | 2663-2718 | **Path traversal** — `skill_name` from URL used unsanitized in path construction. `skill_name = "../../etc"` would resolve `shared-skills/../../etc` outside DATA_ROOT, allowing `copytree` to write to arbitrary locations. Same pattern exists in `delete_skill` and `delete_shared_skill`. |
| 3 | `main_server.py:2882` / `skill_feedback.py:167` | | **Type mismatch** — `skill_version` is `int \| None` in the Pydantic model but `str` in the DB manager. Mixed types in the TEXT column will fail string-based comparisons at query time. |
| 4 | `main_server.py` | 2143-2166 | **Silent empty returns** — `list_sessions` and `get_session_history` return `[]` when DB is unavailable instead of surfacing an error (503 Service Unavailable or logged warning). |
| 5 | `test_evolution_agent.py` | 46 | **Broken test fixture** — `MessageBuffer(base_dir=...)` passes a kwarg the constructor no longer accepts, causing `TypeError` crash for all 16 tests in this file. |
| 6 | `AskUserQuestionCard.tsx` | 35-42, 78-84 | **Duplicated validation** — `canSubmit` and `answeredCount` have identical logic that will drift if validation rules change. Should extract a shared `isAnswered(i)` helper. |
| 7 | `AskUserQuestionCard.tsx` | 53 | **Sentinel leak** — fallback to `'__other__'` when custom input is empty. Dead code at runtime (guarded by `canSubmit`), but should be removed to prevent silent bugs if validation ever becomes misaligned. |

### MEDIUM — 8 issues

| # | File | Line | Issue |
|---|------|------|-------|
| 1 | `main_server.py` | 1142-1192 | `_build_history_prompt` now includes assistant messages — removed explicit exclusion that prevented Echo agents from repeating prior responses. Consider making this configurable via env var. |
| 2 | `main_server.py` | 2699 | `shutil.copytree` in promote has no file count or size limits. Upload endpoint enforces `MAX_ZIP_SIZE=50MB` and `MAX_SKILL_FILES=100`; promote should apply similar guardrails. |
| 3 | `main_server.py` | — | Admin checks removed from all evolution endpoints; `admin_auth.py` is now a complete no-op. Any authenticated user can activate/rollback shared skill versions. Deliberate design decision but team should confirm. |
| 4 | `ChatArea.tsx` | 390 | Error state has no visual feedback — `sessionState === "error"` renders neither spinner nor error message. User cannot distinguish error from idle. |
| 5 | `global.css` | 1069-1131 | New CSS rules use hardcoded colors (`#f0fdf4`, `#22c55e`, `#dc2626`, `rgba(59,130,246,0.15)`) instead of `var(--color-*)` design tokens. Styles will not respond to theme changes. |
| 6 | `test_evolution_agent.py` | 42 | `_patch_data_root` fixture missing `pending_answers.clear()` — present in `test_main_server.py` fixture, absent here. Could cause test state leakage. |
| 7 | `main_server.py` | 1138 | `MAX_CONTINUATION_WINDOW` doubled from 10→20 with no documented rationale in the comment. |
| 8 | `main_server.py` | 2659 | `PromoteRequest` model defined but never used as a request body parameter — dead code. |

### LOW — 4 issues

| # | File | Line | Issue |
|---|------|------|-------|
| 1 | `main_server.py` | — | Logger calls downgraded from `info` to `debug` — less production visibility for stream event diagnostics. |
| 2 | `test_skill_evolution.py` | 6 | Unused `import json` at top of file. |
| 3 | `SkillsPanel.tsx` | 148 | Emoji in button text — inconsistent with project style rules. |
| 4 | `main_server.py` | 125 | `_CLI_SESSION_MAP_FILE` uses shared `DATA_ROOT` path across all users — should be documented. |

---

## Validation Results

| Check | Result |
|-------|--------|
| Unit tests (key files) | 44/45 passed (1 flaky pre-existing) |
| Type check | Skipped (mypy not available) |
| Lint | Skipped (ruff not available) |

---

## Files Reviewed

| File | Status |
|------|--------|
| `main_server.py` | Modified |
| `src/skill_evolution.py` | Modified |
| `src/skill_feedback.py` | Modified |
| `src/admin_auth.py` | Modified |
| `src/database.py` | Modified |
| `src/message_buffer.py` | Modified |
| `src/session_store.py` | Modified |
| `src/learn-extraction.md` | Modified |
| `src/ab_testing.py` | Deleted |
| `src/session_cleanup.py` | Deleted |
| `frontend/src/components/AskUserQuestionCard.tsx` | Modified |
| `frontend/src/components/ChatArea.tsx` | Modified |
| `frontend/src/components/ChatArea.test.tsx` | Modified |
| `frontend/src/components/SkillsPanel.tsx` | Modified |
| `frontend/src/hooks/useSkillsApi.ts` | Modified |
| `frontend/src/styles/global.css` | Modified |
| `tests/unit/test_skill_evolution.py` | Modified |
| `tests/unit/test_skill_promotion.py` | Added |
| `tests/unit/test_evolution_agent.py` | Modified |
| `tests/unit/test_main_server.py` | Modified |
| `tests/unit/test_message_buffer.py` | Modified |
| `tests/unit/test_message_buffer_db.py` | Modified |
| `tests/unit/test_session_store.py` | Modified |
| `tests/unit/test_ab_testing.py` | Deleted |
| `tests/unit/test_session_cleanup.py` | Deleted |
| `tests/integration/conftest.py` | Modified |
| `tests/integration/test_session_endpoints_db.py` | Modified |
| `docs/evolution-feedback-investigation.md` | Added |
| `docs/skill-creation-mechanisms.md` | Added |

---

## Notable Strengths

1. **SQL injection protection** — All database queries use parameterized queries with `?` placeholders.
2. **No hardcoded secrets** — API keys, tokens, and base URLs come from environment variables.
3. **Well-designed retry mechanism** in `_write_db_sync` with exponential backoff.
4. **Clean logging unification** — consolidating all loggers to share one `RotatingFileHandler` fixes the Windows `PermissionError`.
5. **Good immutability patterns** — frozen dataclasses (`FeedbackStats`, `EvolutionCandidate`).
6. **Workspace sandboxing** via PreToolUse hooks and path confinement.

---

## Recommendations

1. **Fix WebSocket user_id** — Use the JWT-verified identity from `_auth_user_id`, not the self-claimed identity from the message body.
2. **Sanitize skill_name** — Reject values containing `.`, `..`, `/`, or `\` in all skill path endpoints.
3. **Fix test_evolution_agent.py fixture** — Change `MessageBuffer(base_dir=...)` to `MessageBuffer()`.
4. **Align skill_version types** — Convert to `str` before passing to the DB manager or change the schema to `INTEGER`.
5. **Extract shared `isAnswered()` helper** in AskUserQuestionCard to eliminate duplicated validation logic.

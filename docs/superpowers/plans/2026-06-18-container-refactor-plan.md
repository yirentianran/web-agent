# Container / Non-Container Mode Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify container and non-container agent execution paths via typed internal protocol layer, shared security enforcer, and extracted executors.

**Architecture:** Introduce `src/agent/` package with typed `InternalEvent` protocol, two thin adapters (SDK + container JSON), unified options/prompt builders, and two `AgentExecutor` implementations. Extract `SecurityEnforcer` to `src/security/` for shared pre-execution checks. Slim `main_server.py` from ~6900 to ~4000 lines.

**Tech Stack:** Python 3.12, FastAPI, asyncio, Claude Agent SDK, pytest

---

### Task 1: Create InternalEvent protocol types (`src/agent/protocol.py`)

**Files:**
- Create: `src/agent/__init__.py`
- Create: `src/agent/protocol.py`

- [ ] **Step 1: Create `src/agent/__init__.py`**

```bash
mkdir -p src/agent/adapters && touch src/agent/__init__.py src/agent/adapters/__init__.py
```

- [ ] **Step 2: Write `src/agent/protocol.py`** with all InternalEvent types

```python
"""Typed internal event protocol for agent execution pipeline.

Both modes (local SDK and container JSON) produce these typed events.
The pipeline (event_pipeline.py) consumes only InternalEvent, never raw dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class AssistantEvent:
    """Text content from the agent."""
    type: Literal["assistant"] = "assistant"
    content: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"type": "assistant", "content": self.content}


@dataclass(frozen=True)
class ToolUseEvent:
    """Tool invocation request."""
    type: Literal["tool_use"] = "tool_use"
    name: str = ""
    id: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    seq: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": "tool_use",
            "name": self.name,
            "id": self.id,
            "input": self.input,
        }
        if self.seq is not None:
            d["seq"] = self.seq
        return d


@dataclass(frozen=True)
class ToolResultEvent:
    """Tool execution result."""
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str = ""
    content: str = ""
    is_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "tool_use_id": self.tool_use_id,
            "content": self.content,
            "is_error": self.is_error,
        }


@dataclass(frozen=True)
class StreamEvent:
    """Streaming delta from agent (content_block_delta, etc.)."""
    type: Literal["stream_event"] = "stream_event"
    event: dict[str, Any] = field(default_factory=dict)
    uuid: str | None = None
    session_id: str | None = None
    index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "stream_event", "event": self.event}
        if self.uuid is not None:
            d["uuid"] = self.uuid
        if self.session_id is not None:
            d["session_id"] = self.session_id
        if self.index is not None:
            d["index"] = self.index
        return d


@dataclass(frozen=True)
class SystemEvent:
    """Lifecycle notifications (timeout, cancel, progress, session_state_changed)."""
    type: Literal["system"] = "system"
    subtype: str = ""
    status: str | None = None
    message: str | None = None
    summary: str | None = None
    usage: dict[str, Any] | None = None
    data: dict[str, Any] | None = None  # extra fields from TaskProgressMessage / SystemMessage

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "system", "subtype": self.subtype}
        if self.status is not None:
            d["status"] = self.status
        if self.message is not None:
            d["message"] = self.message
        if self.summary is not None:
            d["summary"] = self.summary
        if self.usage is not None:
            d["usage"] = self.usage
        if self.data is not None:
            d.update(self.data)
        return d


@dataclass(frozen=True)
class UserEvent:
    """User message (replayed history or new message)."""
    type: Literal["user"] = "user"
    content: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"type": "user", "content": self.content}


@dataclass(frozen=True)
class ResultEvent:
    """Final agent result (usage, stop_reason, duration)."""
    type: Literal["result"] = "result"
    subtype: str | None = None
    duration_ms: float = 0
    usage: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": "result",
            "subtype": self.subtype or "success",
            "duration_ms": self.duration_ms,
            "usage": self.usage,
        }
        if self.model:
            d["model"] = self.model
        d.update({k: v for k, v in self.raw.items() if k not in d})
        return d


@dataclass(frozen=True)
class ErrorEvent:
    """Error message for frontend display."""
    type: Literal["error"] = "error"
    message: str = ""
    subtype: str | None = None  # timeout, cancelled, general

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "error", "message": self.message}
        if self.subtype:
            d["subtype"] = self.subtype
        return d


# Discriminated union
InternalEvent = AssistantEvent | ToolUseEvent | ToolResultEvent | StreamEvent | SystemEvent | UserEvent | ResultEvent | ErrorEvent
```

- [ ] **Step 3: Commit**

```bash
git add src/agent/__init__.py src/agent/protocol.py src/agent/adapters/__init__.py
git commit -m "feat: add InternalEvent protocol types for agent execution pipeline"
```

---

### Task 2: Write unit tests for protocol types

**Files:**
- Create: `tests/unit/test_agent_protocol.py`

- [ ] **Step 1: Create `tests/unit/test_agent_protocol.py`**

```python
"""Tests for InternalEvent protocol types and to_dict serialization."""

from __future__ import annotations

from src.agent.protocol import (
    AssistantEvent,
    ErrorEvent,
    ResultEvent,
    StreamEvent,
    SystemEvent,
    ToolResultEvent,
    ToolUseEvent,
    UserEvent,
)


class TestAssistantEvent:
    def test_to_dict_basic(self) -> None:
        event = AssistantEvent(content="hello world")
        assert event.to_dict() == {"type": "assistant", "content": "hello world"}

    def test_to_dict_empty_content(self) -> None:
        event = AssistantEvent()
        assert event.to_dict() == {"type": "assistant", "content": ""}


class TestToolUseEvent:
    def test_to_dict_full(self) -> None:
        event = ToolUseEvent(
            name="Write",
            id="tool_001",
            input={"file_path": "outputs/hello.txt", "content": "hi"},
            seq=5,
        )
        result = event.to_dict()
        assert result["type"] == "tool_use"
        assert result["name"] == "Write"
        assert result["id"] == "tool_001"
        assert result["input"] == {"file_path": "outputs/hello.txt", "content": "hi"}
        assert result["seq"] == 5

    def test_to_dict_no_seq(self) -> None:
        event = ToolUseEvent(name="Bash", id="tool_002", input={"command": "ls"})
        result = event.to_dict()
        assert "seq" not in result


class TestToolResultEvent:
    def test_to_dict_success(self) -> None:
        event = ToolResultEvent(
            tool_use_id="tool_001", content="Output: 42", is_error=False
        )
        assert event.to_dict() == {
            "type": "tool_result",
            "tool_use_id": "tool_001",
            "content": "Output: 42",
            "is_error": False,
        }

    def test_to_dict_error(self) -> None:
        event = ToolResultEvent(
            tool_use_id="tool_001", content="Permission denied", is_error=True
        )
        assert event.to_dict()["is_error"] is True


class TestStreamEvent:
    def test_to_dict_basic(self) -> None:
        event = StreamEvent(event={"type": "content_block_delta", "delta": {}})
        result = event.to_dict()
        assert result["type"] == "stream_event"
        assert result["event"]["type"] == "content_block_delta"

    def test_to_dict_with_metadata(self) -> None:
        event = StreamEvent(
            event={"type": "content_block_delta", "delta": {}},
            uuid="abc-123",
            session_id="s1",
            index=42,
        )
        result = event.to_dict()
        assert result["uuid"] == "abc-123"
        assert result["session_id"] == "s1"
        assert result["index"] == 42


class TestSystemEvent:
    def test_to_dict_minimal(self) -> None:
        event = SystemEvent(subtype="session_state_changed", status="completed")
        assert event.to_dict() == {
            "type": "system",
            "subtype": "session_state_changed",
            "status": "completed",
        }

    def test_to_dict_with_extra_data(self) -> None:
        event = SystemEvent(subtype="progress", data={"elapsed_sec": 1.5})
        result = event.to_dict()
        assert result["elapsed_sec"] == 1.5

    def test_to_dict_with_usage(self) -> None:
        event = SystemEvent(
            subtype="progress",
            usage={"input_tokens": 100, "output_tokens": 50},
        )
        result = event.to_dict()
        assert result["usage"] == {"input_tokens": 100, "output_tokens": 50}


class TestUserEvent:
    def test_to_dict(self) -> None:
        event = UserEvent(content="Hello, can you help me?")
        assert event.to_dict() == {
            "type": "user",
            "content": "Hello, can you help me?",
        }


class TestResultEvent:
    def test_to_dict_basic(self) -> None:
        event = ResultEvent(
            subtype="success",
            duration_ms=1234.5,
            usage={"input_tokens": 100, "output_tokens": 50},
            model="claude-sonnet-4-6",
        )
        result = event.to_dict()
        assert result["type"] == "result"
        assert result["subtype"] == "success"
        assert result["duration_ms"] == 1234.5
        assert result["model"] == "claude-sonnet-4-6"


class TestErrorEvent:
    def test_to_dict_basic(self) -> None:
        event = ErrorEvent(message="Something went wrong")
        assert event.to_dict() == {
            "type": "error",
            "message": "Something went wrong",
        }

    def test_to_dict_with_subtype(self) -> None:
        event = ErrorEvent(
            message="Agent task timed out", subtype="session_timeout"
        )
        assert event.to_dict()["subtype"] == "session_timeout"
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/unit/test_agent_protocol.py -v
```
Expected: All 12 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_agent_protocol.py
git commit -m "test: add unit tests for InternalEvent protocol types"
```

---

### Task 3: Create `src/security/` package (migrate from security_filter.py)

**Files:**
- Create: `src/security/__init__.py`
- Create: `src/security/filters.py`
- Create: `src/security/rate_limiter.py`

- [ ] **Step 1: Create `src/security/__init__.py`**

```python
"""Security enforcement, filtering, and rate limiting."""
```

- [ ] **Step 2: Create `src/security/rate_limiter.py`** — extract ToolCallRateLimiter

```python
"""Per-session sliding-window rate limiter for tool calls."""

import time


class ToolCallRateLimiter:
    """Per-session sliding-window rate limiter for tool calls.

    Default: 30 calls per 60-second window.
    """

    def __init__(self, max_calls: int = 30, window: float = 60.0) -> None:
        self._max_calls = max_calls
        self._window = window
        self._buckets: dict[str, list[float]] = {}

    def allow(self, session_id: str) -> bool:
        now = time.time()
        bucket = self._buckets.setdefault(session_id, [])
        cutoff = now - self._window
        bucket[:] = [t for t in bucket if t > cutoff]
        if len(bucket) >= self._max_calls:
            return False
        bucket.append(now)
        return True

    def clear(self, session_id: str) -> None:
        self._buckets.pop(session_id, None)


# Module-level singleton for use across the app
tool_call_rate_limiter = ToolCallRateLimiter()
```

- [ ] **Step 3: Create `src/security/filters.py`** — copy OutputFilter, BashCommandFilter, FileAccessFilter from `src/security_filter.py` unchanged

```bash
# Copy the three filter classes from security_filter.py, removing only ToolCallRateLimiter
# Read the current security_filter.py and write filters.py with the same content minus ToolCallRateLimiter
```

Read `src/security_filter.py` lines 1–265 (OutputFilter, BashCommandFilter, FileAccessFilter). Write those three classes into `src/security/filters.py`, adding the `from __future__ import annotations` header and necessary imports (`re`, `time`, `typing.Final`). Exclude `ToolCallRateLimiter` (lines 199–222) and the module-level `tool_call_rate_limiter` singleton (lines 224–225).

- [ ] **Step 4: Update all imports** — change `from src.security_filter import ...` to `from src.security.filters import ...` and `from src.security.rate_limiter import ...`

Files to update:
- `main_server.py:58` — change `from src.security_filter import BashCommandFilter, FileAccessFilter, tool_call_rate_limiter` to `from src.security.filters import BashCommandFilter, FileAccessFilter` and `from src.security.rate_limiter import tool_call_rate_limiter`
- `agent_server.py:44` — change `from src.security_filter import OutputFilter` to `from src.security.filters import OutputFilter`
- `agent_server.py:518,549` — the lazy imports `from src.security_filter import BashCommandFilter` and `from src.security_filter import FileAccessFilter` change to `from src.security.filters import BashCommandFilter` and `from src.security.filters import FileAccessFilter`
- Any other files that import from `src.security_filter`

Run grep to find all importers:
```bash
grep -rn "from src.security_filter import\|import src.security_filter" src/ main_server.py agent_server.py tests/
```

- [ ] **Step 5: Run existing tests to verify no regressions**

```bash
uv run pytest tests/ -x --timeout=30 -q
```
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/security/__init__.py src/security/filters.py src/security/rate_limiter.py
git add -u  # stage modifications to import statements
git commit -m "refactor: extract security filters and rate limiter to src/security/ package"
```

---

### Task 4: Create SecurityEnforcer (`src/security/enforcer.py`)

**Files:**
- Create: `src/security/enforcer.py`

- [ ] **Step 1: Create `src/security/enforcer.py`**

```python
"""Shared pre-execution security enforcement.

Used by both local mode (via SDK hooks) and container mode (via
agent_server control_request handlers). Single implementation ensures
consistent behavior across modes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.security.filters import BashCommandFilter, FileAccessFilter

logger = logging.getLogger(__name__)

_INVALID_FILENAMES = frozenset({"null", "undefined", "none", ""})


@dataclass
class SecurityEnforcer:
    """Shared pre-execution security checks for agent tool calls.

    ``user_id``, ``workspace``, and ``user_dir`` define the sandbox
    boundaries. All checks use these to validate paths and commands.

    Used by:
    - LocalAgentExecutor: builds SDK can_use_tool / PreToolUse hooks
    - agent_server._CliRunner: control_request hook_callback handler
    """

    user_id: str
    workspace: Path
    user_dir: Path

    def check_bash(self, command: str) -> tuple[bool, str]:
        """Check if a bash command is safe to execute.

        Returns (True, "") if allowed, (False, reason) if denied.
        """
        if not command or not command.strip():
            return False, "Empty command"
        allowed, reason = BashCommandFilter.check(command)
        if not allowed:
            logger.debug(
                "SecurityEnforcer[Bash]: blocked command for user %s: %s",
                self.user_id,
                reason,
            )
        return allowed, reason

    def check_write_path(self, file_path: str) -> tuple[bool, str]:
        """Check if a file write path is within the allowed sandbox.

        Returns (True, "") if allowed, (False, reason) if denied.
        """
        if not file_path or file_path.lower() in _INVALID_FILENAMES:
            logger.warning(
                "SecurityEnforcer[Write]: blocked invalid file_path '%s' for user %s",
                file_path,
                self.user_id,
            )
            return False, f"Invalid file path: '{file_path}'. Please provide a real filename."

        allowed, reason = FileAccessFilter.check(file_path)
        if not allowed:
            logger.debug(
                "SecurityEnforcer[Write]: blocked sensitive file '%s' for user %s",
                file_path,
                self.user_id,
            )
            return False, reason

        return True, ""

    def check_read_path(self, file_path: str) -> tuple[bool, str]:
        """Check if a file read path is allowed.

        Returns (True, "") if allowed, (False, reason) if denied.
        """
        if not file_path:
            return False, "Empty path"

        allowed, reason = FileAccessFilter.check(file_path)
        if not allowed:
            logger.debug(
                "SecurityEnforcer[Read]: blocked sensitive file '%s' for user %s",
                file_path,
                self.user_id,
            )
        return allowed, reason

    def check_read_size(
        self, file_path: str, max_bytes: int, cwd: str | None = None
    ) -> tuple[bool, str]:
        """Check if a file is within the allowed read size limit.

        Returns (True, "") if allowed, (False, reason) if denied.
        """
        if not file_path or max_bytes <= 0:
            return True, ""

        try:
            resolved = Path(file_path)
            if not resolved.is_absolute() and cwd:
                resolved = Path(cwd) / file_path
            file_size = resolved.stat().st_size
            if file_size > max_bytes:
                size_mb = file_size / (1024 * 1024)
                limit_mb = max_bytes / (1024 * 1024)
                return False, (
                    f"File is {size_mb:.1f}MB. "
                    f"The maximum allowed size for reading "
                    f"is {limit_mb:.0f}MB. Please use Bash "
                    f"commands like 'head' or 'split' to "
                    f"process the file in smaller chunks."
                )
        except OSError:
            pass  # file doesn't exist — let CLI handle it

        return True, ""

    def build_write_input_allow(
        self, tool_input: dict[str, Any]
    ) -> dict[str, tuple[bool, str]]:
        """Return per-field permission results for a Write tool input.

        Used to build PreToolUse hook response that may deny the write.
        """
        file_path = str(tool_input.get("file_path", ""))
        allowed, reason = self.check_write_path(file_path)
        return {"file_path": (allowed, reason)}

    def build_bash_allow(
        self, tool_input: dict[str, Any]
    ) -> tuple[bool, str]:
        """Return permission result for a Bash tool input."""
        cmd = str(tool_input.get("command", ""))
        return self.check_bash(cmd)

    def build_read_allow(
        self,
        tool_input: dict[str, Any],
        max_bytes: int = 0,
        cwd: str | None = None,
    ) -> dict[str, tuple[bool, str]]:
        """Return per-field permission results for a Read tool input."""
        file_path = str(tool_input.get("file_path", ""))
        path_allowed, path_reason = self.check_read_path(file_path)
        if not path_allowed:
            return {"file_path": (False, path_reason)}
        size_allowed, size_reason = self.check_read_size(file_path, max_bytes, cwd)
        if not size_allowed:
            return {"file_size": (False, size_reason)}
        return {"file_path": (True, "")}
```

- [ ] **Step 2: Commit**

```bash
git add src/security/enforcer.py
git commit -m "feat: add SecurityEnforcer for shared pre-execution security checks"
```

---

### Task 5: Write tests for SecurityEnforcer

**Files:**
- Create: `tests/unit/test_security_enforcer.py`

- [ ] **Step 1: Create `tests/unit/test_security_enforcer.py`**

```python
"""Tests for SecurityEnforcer shared pre-execution security checks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.security.enforcer import SecurityEnforcer


@pytest.fixture
def enforcer(tmp_path: Path) -> SecurityEnforcer:
    return SecurityEnforcer(
        user_id="test_user",
        workspace=tmp_path / "workspace",
        user_dir=tmp_path / "user_data",
    )


class TestCheckBash:
    def test_allows_safe_command(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_bash("ls -la")
        assert allowed is True
        assert reason == ""

    def test_denies_empty_command(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_bash("")
        assert allowed is False
        assert "Empty" in reason

    def test_denies_env_command(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_bash("env")
        assert allowed is False

    def test_denies_curl(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_bash("curl http://example.com")
        assert allowed is False

    def test_allows_git_diff(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_bash("git diff HEAD~1")
        assert allowed is True


class TestCheckWritePath:
    def test_allows_normal_path(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_write_path("outputs/report.txt")
        assert allowed is True

    def test_denies_empty_path(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_write_path("")
        assert allowed is False

    def test_denies_null_path(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_write_path("null")
        assert allowed is False

    def test_denies_undefined_path(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_write_path("undefined")
        assert allowed is False

    def test_denies_env_file(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_write_path(".env")
        assert allowed is False

    def test_denies_dockerfile(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_write_path("Dockerfile")
        assert allowed is False


class TestCheckReadPath:
    def test_allows_normal_path(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_read_path("outputs/report.txt")
        assert allowed is True

    def test_denies_empty_path(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_read_path("")
        assert allowed is False

    def test_denies_env_file(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_read_path(".env.local")
        assert allowed is False

    def test_allows_py_file(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_read_path("src/main.py")
        assert allowed is True


class TestCheckReadSize:
    def test_allows_small_file(self, enforcer: SecurityEnforcer, tmp_path: Path) -> None:
        f = tmp_path / "small.txt"
        f.write_text("hello")
        allowed, reason = enforcer.check_read_size(str(f), max_bytes=1024 * 1024)
        assert allowed is True

    def test_denies_oversized_file(self, enforcer: SecurityEnforcer, tmp_path: Path) -> None:
        f = tmp_path / "large.txt"
        f.write_text("x" * 1000)
        allowed, reason = enforcer.check_read_size(str(f), max_bytes=10)
        assert allowed is False
        assert "MB" in reason

    def test_allows_when_max_bytes_zero(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_read_size("/nonexistent/path", max_bytes=0)
        assert allowed is True

    def test_allows_nonexistent_file(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_read_size("/nonexistent/path.txt", max_bytes=1024)
        assert allowed is True
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/unit/test_security_enforcer.py -v
```
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_security_enforcer.py
git commit -m "test: add unit tests for SecurityEnforcer"
```

---

### Task 6: Create SDK adapter (`src/agent/adapters/sdk.py`)

**Files:**
- Create: `src/agent/adapters/sdk.py`

- [ ] **Step 1: Write `src/agent/adapters/sdk.py`** — extracts SDK branch from `message_to_dicts()`

```python
"""Adapter: Claude Agent SDK dataclass messages → InternalEvent.

Extracts the SDK-type branches from the existing ``message_to_dicts()``
and converts each SDK message type into typed InternalEvent instances.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from typing import Any

from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    StreamEvent as SdkStreamEvent,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    UserMessage,
)

from src.agent.adapters.container_json import _process_blocks
from src.agent.protocol import (
    AssistantEvent,
    ResultEvent,
    StreamEvent,
    SystemEvent,
    ToolResultEvent,
    ToolUseEvent,
    UserEvent,
)

# Re-export for container_json adapter
__all__ = ["adapt_sdk_message", "_process_blocks"]


def adapt_sdk_message(
    msg: Any,
    model: str | None = None,
    tool_use_names: dict[str, str] | None = None,
) -> Iterator[Any]:  # Iterator[InternalEvent] — Any avoids circular import
    """Convert an SDK message dataclass to InternalEvent instances.

    Yields typed InternalEvent objects. The ``_process_blocks`` helper
    handles content block extraction for assistant/user messages.
    """
    from src.agent.protocol import InternalEvent  # noqa: PLC0415 — lazy for test mocks

    if tool_use_names is None:
        tool_use_names = {}

    if isinstance(msg, UserMessage):
        content = msg.content
        if isinstance(content, list):
            emitted: list[InternalEvent] = []
            combined_text = _process_blocks(content, emitted, tool_use_names)
            yield from emitted
            text = combined_text
        else:
            text = content
        if text:
            yield UserEvent(content=text)
        return

    if isinstance(msg, AssistantMessage):
        emitted: list[InternalEvent] = []
        combined_text = _process_blocks(msg.content, emitted, tool_use_names)
        yield from emitted
        if combined_text:
            yield AssistantEvent(content=combined_text)
        return

    if isinstance(msg, ResultMessage):
        from src.agent_result import parse_agent_result  # noqa: PLC0415

        result_data = parse_agent_result(dataclasses.asdict(msg), model=model)
        yield ResultEvent(
            subtype=result_data.get("subtype"),
            duration_ms=result_data.get("duration_ms", 0),
            usage=result_data.get("usage", {}),
            model=result_data.get("model"),
            raw=result_data,
        )
        return

    if isinstance(msg, TaskNotificationMessage):
        data: dict[str, Any] = {}
        if msg.usage:
            data["usage"] = dict(msg.usage)
            if model:
                data["usage"]["model"] = model
        if msg.summary:
            data["summary"] = msg.summary
        yield SystemEvent(
            subtype=msg.subtype or "",
            status=msg.status,
            summary=msg.summary,
            usage=data.get("usage"),
            data=data,
        )
        return

    if isinstance(msg, TaskProgressMessage):
        data: dict[str, Any] = {}
        if msg.usage:
            data["usage"] = dict(msg.usage)
            if model:
                data["usage"]["model"] = model
        if msg.data:
            data.update(msg.data)
        yield SystemEvent(subtype="progress", usage=data.get("usage"), data=data)
        return

    if isinstance(msg, SystemMessage):
        data: dict[str, Any] = {}
        if msg.data:
            data.update(msg.data)
        yield SystemEvent(subtype=msg.subtype, data=data)
        return

    if isinstance(msg, SdkStreamEvent):
        evt = msg.event if isinstance(msg.event, dict) else {}
        yield StreamEvent(
            event=evt,
            uuid=getattr(msg, "uuid", None),
            session_id=getattr(msg, "session_id", None),
            index=evt.get("index"),
        )
        return

    # Fallback: unknown type
    if hasattr(msg, "__dict__"):
        yield SystemEvent(subtype="unknown", data={"raw": msg.__dict__})
```

- [ ] **Step 2: Create `_process_blocks` helper in `src/agent/adapters/container_json.py`** (shared between both adapters)

```python
"""Adapter: container WebSocket JSON dict → InternalEvent.

Extracts the dict-type branches from the existing ``message_to_dicts()``
and converts each container JSON message into typed InternalEvent instances.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from src.block_processor import process_content_blocks
from src.agent.protocol import (
    AssistantEvent,
    ResultEvent,
    StreamEvent,
    ToolResultEvent,
    ToolUseEvent,
    UserEvent,
)


def _process_blocks(
    blocks: list[Any],
    emitted: list[Any],  # list[InternalEvent]
    tool_use_names: dict[str, str],
) -> str:
    """Process content blocks, appending ToolUseEvent/ToolResultEvent to emitted.

    Returns combined text for AssistantEvent/UserEvent content.
    Shared between sdk.py and container_json.py adapters.
    """
    from src.agent.protocol import InternalEvent  # noqa: PLC0415

    text_parts: list[str] = []
    try:
        from claude_agent_sdk.types import (
            TextBlock,
            ThinkingBlock,
            ToolResultBlock,
            ToolUseBlock,
        )
    except ImportError:
        TextBlock = ThinkingBlock = ToolUseBlock = ToolResultBlock = None

    # First pass: build tool_use_names mapping
    for block in blocks:
        if ToolUseBlock and isinstance(block, ToolUseBlock):
            tool_use_names[block.id] = block.name
        elif isinstance(block, dict) and block.get("type") == "tool_use":
            tool_use_names[block.get("id", "")] = block.get("name", "")

    # Second pass: emit events
    for block in blocks:
        if TextBlock and isinstance(block, TextBlock):
            text_parts.append(block.text)
        elif ThinkingBlock and isinstance(block, ThinkingBlock):
            text_parts.append(f"[thinking] {block.thinking}[/thinking]")
        elif ToolUseBlock and isinstance(block, ToolUseBlock):
            emitted.append(ToolUseEvent(
                name=block.name,
                id=block.id,
                input=block.input,
            ))
        elif ToolResultBlock and isinstance(block, ToolResultBlock):
            tool_name = tool_use_names.get(block.tool_use_id, "unknown")
            content = block.content
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if isinstance(item, dict):
                        parts.append(item.get("text", str(item)))
                    else:
                        parts.append(str(item))
                content = "\n".join(parts)
            emitted.append(ToolResultEvent(
                tool_use_id=block.tool_use_id,
                content=str(content) if content else "",
                is_error=block.is_error if hasattr(block, "is_error") else False,
            ))
        elif isinstance(block, dict):
            bt = block.get("type", "")
            if bt == "text":
                text_parts.append(block.get("text", ""))
            elif bt == "thinking":
                text_parts.append(f"[thinking] {block.get('thinking', '')}[/thinking]")
            elif bt == "tool_use":
                emitted.append(ToolUseEvent(
                    name=block.get("name", ""),
                    id=block.get("id", ""),
                    input=block.get("input", {}),
                ))
            elif bt == "tool_result":
                content = block.get("content", "")
                if isinstance(content, list):
                    content = "".join(
                        item.get("text", "") if isinstance(item, dict) else str(item)
                        for item in content
                    )
                emitted.append(ToolResultEvent(
                    tool_use_id=block.get("tool_use_id", ""),
                    content=str(content),
                    is_error=block.get("is_error", False),
                ))

    return "".join(text_parts)


def adapt_container_message(
    data: dict[str, Any],
    model: str | None = None,
    tool_use_names: dict[str, str] | None = None,
) -> Iterator[Any]:  # Iterator[InternalEvent]
    """Convert a container WebSocket JSON dict to InternalEvent instances."""
    from src.agent.protocol import InternalEvent  # noqa: PLC0415

    if tool_use_names is None:
        tool_use_names = {}

    msg_type = data.get("type", "")

    if msg_type == "assistant":
        message = data.get("message", {})
        if message:
            content_blocks = message.get("content", [])
            emitted: list[InternalEvent] = []
            combined_text = _process_blocks(content_blocks, emitted, tool_use_names)
            yield from emitted
            if combined_text:
                yield AssistantEvent(content=combined_text)
        return

    if msg_type == "user":
        message = data.get("message", {})
        if message:
            content_blocks = message.get("content", [])
            emitted: list[InternalEvent] = []
            combined_text = _process_blocks(content_blocks, emitted, tool_use_names)
            yield from emitted
            text = combined_text
        else:
            text = data.get("content", "")
        if text:
            yield UserEvent(content=text)
        return

    if msg_type == "stream_event":
        yield StreamEvent(event=data.get("event", {}))
        return

    if msg_type == "result":
        from src.agent_result import parse_agent_result  # noqa: PLC0415

        result_data = parse_agent_result(data, model=model)
        yield ResultEvent(
            subtype=result_data.get("subtype"),
            duration_ms=result_data.get("duration_ms", 0),
            usage=result_data.get("usage", {}),
            model=result_data.get("model"),
            raw=result_data,
        )
        return

    # Unknown dict type — ignore
```

Wait, the above is both adapter files. Let me split properly:

**Write `src/agent/adapters/container_json.py`** with `_process_blocks()` and `adapt_container_message()`.

**Write `src/agent/adapters/sdk.py`** with `adapt_sdk_message()` that imports `_process_blocks` from `container_json`.

- [ ] **Step 3: Verify current message_to_dicts behavior preserved** — this is a structural move, no behavior change. Verify by running existing tests. The adapters are not yet wired in—tests pass because old code still in place.

- [ ] **Step 4: Commit**

```bash
git add src/agent/adapters/container_json.py src/agent/adapters/sdk.py
git commit -m "feat: add SDK and container JSON adapters for InternalEvent conversion"
```

---

### Task 7: Write adapter unit tests

**Files:**
- Create: `tests/unit/test_agent_adapters.py`

- [ ] **Step 1: Create `tests/unit/test_agent_adapters.py`**

```python
"""Tests for SDK and container JSON adapters → InternalEvent conversion."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.agent.adapters.container_json import (
    _process_blocks,
    adapt_container_message,
)
from src.agent.adapters.sdk import adapt_sdk_message
from src.agent.protocol import (
    AssistantEvent,
    StreamEvent,
    ToolUseEvent,
    UserEvent,
)


class TestAdaptContainerMessage:
    def test_assistant_with_text_content(self) -> None:
        data = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Hello from container"}],
            },
        }
        events = list(adapt_container_message(data))
        assert len(events) == 1
        assert isinstance(events[0], AssistantEvent)
        assert events[0].content == "Hello from container"

    def test_assistant_with_tool_use_block(self) -> None:
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Bash", "id": "tu1", "input": {"command": "ls"}},
                ],
            },
        }
        events = list(adapt_container_message(data))
        assert len(events) == 1
        assert isinstance(events[0], ToolUseEvent)
        assert events[0].name == "Bash"

    def test_stream_event(self) -> None:
        data = {
            "type": "stream_event",
            "event": {"type": "content_block_delta", "delta": {"text": "hi"}},
        }
        events = list(adapt_container_message(data))
        assert len(events) == 1
        assert isinstance(events[0], StreamEvent)

    def test_user_message(self) -> None:
        data = {"type": "user", "message": {"content": [{"type": "text", "text": "query"}]}}
        events = list(adapt_container_message(data))
        assert len(events) == 1
        assert isinstance(events[0], UserEvent)
        assert events[0].content == "query"

    def test_result_message(self) -> None:
        data = {
            "type": "result",
            "subtype": "success",
            "duration_ms": 500,
            "usage": {"input_tokens": 10},
            "is_error": False,
        }
        with patch("src.agent_result.parse_agent_result", return_value={
            "type": "result", "subtype": "success", "duration_ms": 500,
            "usage": {"input_tokens": 10},
        }):
            events = list(adapt_container_message(data))
            assert len(events) == 1
            assert events[0].type == "result"

    def test_unknown_type_ignored(self) -> None:
        events = list(adapt_container_message({"type": "unknown_xyz", "data": {}}))
        assert len(events) == 0


class TestAdaptSdkMessage:
    def test_stream_event_conversion(self) -> None:
        """Verify SDK StreamEvent is converted correctly."""
        sdk_event = MagicMock()
        sdk_event.event = {"type": "content_block_delta", "delta": {"text": "hi"}}
        sdk_event.uuid = "uuid-1"
        sdk_event.session_id = "s1"

        with patch("claude_agent_sdk.types.StreamEvent", type(sdk_event)):
            events = list(adapt_sdk_message(sdk_event))
            assert len(events) == 1
            assert isinstance(events[0], StreamEvent)
            assert events[0].uuid == "uuid-1"

    def test_user_message_with_text_content(self) -> None:
        from claude_agent_sdk.types import UserMessage

        msg = UserMessage(content="hello")
        events = list(adapt_sdk_message(msg))
        assert len(events) == 1
        assert isinstance(events[0], UserEvent)
        assert events[0].content == "hello"


class TestProcessBlocks:
    def test_process_text_block_dict(self) -> None:
        blocks = [{"type": "text", "text": "hello world"}]
        emitted: list = []
        names: dict[str, str] = {}
        result = _process_blocks(blocks, emitted, names)
        assert result == "hello world"
        assert len(emitted) == 0

    def test_process_tool_use_block_dict(self) -> None:
        blocks = [{"type": "tool_use", "name": "Bash", "id": "tu1", "input": {}}]
        emitted: list = []
        names: dict[str, str] = {}
        result = _process_blocks(blocks, emitted, names)
        assert result == ""
        assert len(emitted) == 1
        assert isinstance(emitted[0], ToolUseEvent)
        assert emitted[0].name == "Bash"
        assert names == {"tu1": "Bash"}

    def test_process_mixed_blocks(self) -> None:
        blocks = [
            {"type": "text", "text": "Let me run: "},
            {"type": "tool_use", "name": "Bash", "id": "tu1", "input": {}},
        ]
        emitted: list = []
        names: dict[str, str] = {}
        result = _process_blocks(blocks, emitted, names)
        assert result == "Let me run: "
        assert len(emitted) == 1
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/unit/test_agent_adapters.py -v
```
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_agent_adapters.py
git commit -m "test: add unit tests for agent adapters"
```

---

### Task 8: Create unified options builder (`src/agent/options.py`)

**Files:**
- Create: `src/agent/options.py`

- [ ] **Step 1: Write `src/agent/options.py`**

```python
"""Unified AgentOptions builder — serves both local and container modes.

Merges ``build_sdk_options()`` and ``build_container_options_dict()``
into a single builder. The returned ``AgentOptions`` dataclass is converted
to SDK-specific ``ClaudeAgentOptions`` by ``LocalAgentExecutor`` or
serialized to a JSON dict by ``ContainerAgentExecutor``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class AgentOptions:
    """Unified agent configuration — consumed by both executor implementations."""

    model: str = ""
    system_prompt: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: set[str] = field(default_factory=set)
    max_turns: int = 200
    mcp_servers: dict[str, Any] | None = None
    env: dict[str, str] | None = None
    include_partial_messages: bool = True
    max_buffer_size: int = 10 * 1024 * 1024
    permission_mode: str = "acceptEdits"
    cwd: str | None = None  # container-only: workspace path inside container
    resume_session_id: str | None = None
    # SDK-specific: path to user data dir for HOME override (local mode only)
    user_data_dir: str | None = None
    # SDK-specific: skills dir (local mode only)
    skills_dir: str | None = None


async def build_agent_options(
    user_id: str,
    *,
    skills_override: dict[str, dict[str, Any]] | None = None,
    system_prompt_override: str | None = None,
    resume_session_id: str | None = None,
    language: str | None = None,
    container_mode: bool = False,
) -> AgentOptions:
    """Build unified AgentOptions for both local and container modes.

    Calls ``_build_sdk_config()`` from main_server for the shared config
    layer, then constructs the appropriate AgentOptions depending on mode.
    """
    from main_server import (  # noqa: PLC0415 — lazy to avoid circular import at module level
        _build_sdk_config,
        _get_container_manager,
        build_allowed_tools,
        build_system_prompt,
        load_mcp_config,
        load_skills,
        user_data_dir,
        user_workspace_dir,
    )

    mcp_config = await load_mcp_config()
    if skills_override is not None:
        skills = skills_override
    else:
        skills = await load_skills(user_id)

    workspace = user_workspace_dir(user_id)

    cfg = await _build_sdk_config(
        user_id,
        mcp_config,
        skills,
        workspace,
        language,
        user_data_dir_override=user_data_dir(user_id) if not container_mode else None,
        system_prompt_override=system_prompt_override,
    )

    options = AgentOptions(
        model=cfg["model"] or "",
        system_prompt=cfg["system_prompt"],
        allowed_tools=cfg["allowed_tools"],
        disallowed_tools=cfg["disallowed_tools"],
        max_turns=cfg["max_turns"],
        mcp_servers=cfg["mcp_servers"],
        env=cfg["sdk_env"],
        include_partial_messages=cfg["include_partial_messages"],
        max_buffer_size=cfg["max_buffer_size"],
        permission_mode="acceptEdits",
        resume_session_id=resume_session_id,
    )

    if container_mode:
        cm = _get_container_manager()
        options = dataclasses.replace(options, cwd=str(
            cm.container_workspace_dir(user_id)
        ) if cm else "/workspace")
    else:
        options = dataclasses.replace(
            options,
            user_data_dir=str(user_data_dir(user_id)),
            skills_dir=str(workspace / ".claude" / "skills"),
        )

    return options
```

Wait, I need to import `dataclasses`:

```python
import dataclasses
```

- [ ] **Step 2: Commit**

```bash
git add src/agent/options.py
git commit -m "feat: add unified AgentOptions builder"
```

---

### Task 9: Create prompt builder (`src/agent/prompt.py`)

**Files:**
- Create: `src/agent/prompt.py`

- [ ] **Step 1: Write `src/agent/prompt.py`** — extract `_build_history_prompt()` and `_format_first_message_prompt()` from main_server.py

Copy the two functions exactly from `main_server.py:1955-2109` into `src/agent/prompt.py`, keeping all logic identical. Add:

```python
"""Prompt formatting — history compilation, language directives, attachments."""

from __future__ import annotations

import os
import re
from typing import Any

MAX_CONTINUATION_WINDOW = int(os.getenv("MAX_CONTINUATION_WINDOW", "50"))
MAX_PROMPT_LENGTH = int(os.getenv("MAX_PROMPT_LENGTH", "32000"))
_TOOL_RESULT_MAX_CHARS = int(os.getenv("TOOL_RESULT_MAX_CHARS", "500"))


def build_history_prompt(
    history: list[dict[str, Any]],
    user_message: str,
    language: str | None = None,
    session_id: str | None = None,
) -> str:
    """..."""  # Same implementation as current _build_history_prompt


def format_first_message_prompt(
    user_message: str,
    attached_files: list[str] | None,
    language: str | None = None,
    session_id: str | None = None,
) -> str:
    """..."""  # Same implementation as current _format_first_message_prompt
```

The full implementation copies `main_server.py` lines 1955–2109 verbatim, renaming `_build_history_prompt` → `build_history_prompt` and `_format_first_message_prompt` → `format_first_message_prompt`.

- [ ] **Step 2: Commit**

```bash
git add src/agent/prompt.py
git commit -m "refactor: extract prompt builder to src/agent/prompt.py"
```

---

### Task 10: Add shared error handler to event_pipeline.py

**Files:**
- Modify: `src/event_pipeline.py`

- [ ] **Step 1: Add `handle_task_error()` to `src/event_pipeline.py`**

Append after `_finish_task()` (after line 195):

```python
async def handle_task_error(
    error: Exception,
    *,
    session_id: str,
    user_id: str,
    buffer: Any,
    obs_store: Any,
    agent_log: Any,
    cleanup_fn: Any | None = None,  # async callable for cleanup (e.g. close bridge/client)
) -> None:
    """Shared error handling for both local and container executors.

    Handles: TimeoutError, asyncio.CancelledError, and generic Exception.
    Emits appropriate error messages, state changes, and marks session done.
    """
    import asyncio  # noqa: PLC0415

    if isinstance(error, TimeoutError):
        if cleanup_fn is not None:
            try:
                await cleanup_fn(session_id)
            except Exception:
                pass
        await buffer.add_message(
            session_id,
            {
                "type": "system",
                "subtype": "session_timeout",
                "message": "Agent task timed out. The agent may be stuck processing a file.",
            },
            user_id,
        )
        await buffer.add_message(
            session_id,
            {"type": "system", "subtype": "session_state_changed", "state": "error"},
            user_id,
        )
        await buffer.mark_done(session_id)
        agent_log.end_session(session_id, status="timeout")
        if obs_store:
            await obs_store.record(
                session_id=session_id,
                user_id=user_id,
                event_type="session_error",
                success=False,
                error_message="timeout",
            )

    elif isinstance(error, asyncio.CancelledError):
        await buffer.add_message(
            session_id,
            {
                "type": "system",
                "subtype": "session_cancelled",
                "message": "Session cancelled by user.",
            },
            user_id,
        )
        await buffer.add_message(
            session_id,
            {"type": "system", "subtype": "session_state_changed", "state": "cancelled"},
            user_id,
        )
        await buffer.mark_done(session_id)
        agent_log.end_session(session_id, status="cancelled")
        if obs_store:
            await obs_store.record(
                session_id=session_id,
                user_id=user_id,
                event_type="user_interrupt",
                success=False,
            )

    else:
        error_msg = str(error)
        if "JSON message exceeded maximum buffer size" in error_msg:
            error_msg = (
                "A tool produced too much output and was truncated to avoid "
                "overwhelming the system. Try narrowing your request or "
                "processing the data in smaller steps."
            )
        if cleanup_fn is not None:
            try:
                await cleanup_fn(session_id)
            except Exception:
                pass
        await buffer.add_message(
            session_id,
            {"type": "error", "message": error_msg},
            user_id,
        )
        await buffer.add_message(
            session_id,
            {"type": "system", "subtype": "session_state_changed", "state": "error"},
            user_id,
        )
        await buffer.mark_done(session_id)
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

- [ ] **Step 2: Commit**

```bash
git add src/event_pipeline.py
git commit -m "feat: add shared handle_task_error to event_pipeline"
```

---

### Task 11: Create LocalAgentExecutor (`src/agent/local.py`)

**Files:**
- Create: `src/agent/local.py`

- [ ] **Step 1: Write `src/agent/local.py`** — extracts `run_agent_task()` from main_server.py

This file contains the LocalAgentExecutor class that wraps ClaudeSDKClient. It extracts all the logic from `run_agent_task()` (main_server.py lines 2279–2648), refactors into a class, and uses the adapters + shared error handler.

```python
"""LocalAgentExecutor — runs agent via ClaudeSDKClient directly in-process.

Used when CONTAINER_MODE=false. Wraps ClaudeSDKClient lifecycle (connect,
query, receive_response), feeds SDK messages through adapt_sdk_message(),
and emits InternalEvent to the shared event pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from claude_agent_sdk import CLIConnectionError, ClaudeSDKClient
from claude_agent_sdk.types import (
    ClaudeAgentOptions,
    HookInput,
    HookContext,
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from src.agent.adapters.sdk import adapt_sdk_message
from src.agent.options import AgentOptions, build_agent_options
from src.agent.prompt import build_history_prompt, format_first_message_prompt
from src.agent.protocol import InternalEvent
from src.event_pipeline import EventContext, _finish_task, handle_task_error, process_event
from src.observation import ToolObserver
from src.security.enforcer import SecurityEnforcer
from src.security.filters import BashCommandFilter, FileAccessFilter
from src.security.rate_limiter import tool_call_rate_limiter
from src.workspace_enforcement import (
    check_bash_command_for_external_writes,
    is_path_within_user_dir,
)

logger = logging.getLogger(__name__)


class LocalAgentExecutor:
    """Execute agent tasks via ClaudeSDKClient in the main process."""

    def __init__(
        self,
        user_id: str,
        session_agents: dict[str, dict[str, Any]],
        buffer: Any,
        session_store: Any,
        skill_manager: Any,
        obs_store: Any,
        db: Any,
        pending_answers: dict[str, asyncio.Future],
        user_workspace_dir_fn: Any,
        user_data_dir_fn: Any,
        snapshot_output_files_fn: Any,
        get_cached_skills_fn: Any,
        get_cached_system_prompt_fn: Any,
        resolve_user_language_fn: Any,
        load_instinct_context_fn: Any,
        cleanup_fn: Any,
    ) -> None:
        self.user_id = user_id
        self._session_agents = session_agents
        self._buffer = buffer
        self._session_store = session_store
        self._skill_manager = skill_manager
        self._obs_store = obs_store
        self._db = db
        self._pending_answers = pending_answers
        self._user_workspace_dir = user_workspace_dir_fn
        self._user_data_dir = user_data_dir_fn
        self._snapshot_output_files = snapshot_output_files_fn
        self._get_cached_skills = get_cached_skills_fn
        self._get_cached_system_prompt = get_cached_system_prompt_fn
        self._resolve_user_language = resolve_user_language_fn
        self._load_instinct_context = load_instinct_context_fn
        self._cleanup = cleanup_fn

    async def run(
        self,
        prompt: str,
        session_id: str,
        is_continuation: bool = False,
        attached_files: list[str] | None = None,
        language: str | None = None,
    ) -> None:
        """Execute an agent task via ClaudeSDKClient."""
        from src.agent_logger import AgentLogger  # noqa: PLC0415
        from src.constants import DISABLED_TOOLS  # noqa: PLC0415
        import uuid  # noqa: PLC0415

        agent_log = AgentLogger(user_id=self.user_id)
        agent_log.start_session(session_id, user_message=prompt)
        start_time = time.time()

        workspace = self._user_workspace_dir(self.user_id)
        user_dir = self._user_data_dir(self.user_id)
        enforcer = SecurityEnforcer(
            user_id=self.user_id, workspace=workspace, user_dir=user_dir,
        )

        # ── Build permission callback with SecurityEnforcer ──
        async def can_use_tool_cb(
            tool_name: str,
            tool_input: dict[str, Any],
            ctx: ToolPermissionContext,
        ) -> PermissionResult:
            if not tool_call_rate_limiter.allow(session_id):
                return PermissionResultDeny(
                    message="Tool call rate limit exceeded. Please wait before making more tool calls.",
                )
            if tool_name in DISABLED_TOOLS:
                return PermissionResultDeny(
                    message=f"{tool_name} is disabled. Use MCP fetch tools instead.",
                )
            if tool_name == "Write":
                file_path = str(tool_input.get("file_path", ""))
                if file_path and not is_path_within_user_dir(file_path, self.user_id):
                    return PermissionResultDeny(
                        message=f"File path '{file_path}' is outside the user directory.",
                    )
            if tool_name == "Bash":
                cmd = str(tool_input.get("command", ""))
                error = check_bash_command_for_external_writes(cmd, workspace, user_dir)
                if error:
                    return PermissionResultDeny(message=error)
                allowed, reason = enforcer.check_bash(cmd)
                if not allowed:
                    return PermissionResultDeny(message=reason)
            if tool_name == "Read":
                file_path = str(tool_input.get("file_path", ""))
                if file_path:
                    allowed, reason = enforcer.check_read_path(file_path)
                    if not allowed:
                        return PermissionResultDeny(message=reason)

            agent_log.tool_call(tool_name, tool_input, session_id=session_id)

            # AskUserQuestion handling
            if tool_name == "AskUserQuestion":
                await self._buffer.add_message(
                    session_id,
                    {
                        "type": "tool_use",
                        "name": "AskUserQuestion",
                        "id": f"ask_{uuid.uuid4().hex[:8]}",
                        "input": tool_input,
                    },
                    self.user_id,
                )
                answer_future: asyncio.Future = asyncio.get_event_loop().create_future()
                self._pending_answers[session_id] = answer_future
                try:
                    answer = await asyncio.wait_for(answer_future, timeout=300)
                    return PermissionResultAllow(
                        behavior="allow",
                        updated_input={"answers": answer},
                    )
                except TimeoutError:
                    return PermissionResultAllow(
                        behavior="allow",
                        updated_input={"answers": {"error": "timeout"}},
                    )
                finally:
                    self._pending_answers.pop(session_id, None)

            return PermissionResultAllow(behavior="allow")

        # ── PreToolUse hook for Write path rewriting ──
        async def write_path_hook(
            hook_input: HookInput,
            _tool_use_id: str | None,
            _context: HookContext,
        ) -> dict:
            tool_inp = hook_input.get("tool_input", {})
            file_path = str(tool_inp.get("file_path", ""))
            allowed, reason = enforcer.check_write_path(file_path)
            if not allowed:
                return {
                    "sync": True,
                    "continue_": True,
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "decision": "reject",
                        "reason": reason,
                    },
                }
            # Path rewriting for workspace enforcement
            from src.workspace_enforcement import normalize_write_path  # noqa: PLC0415
            if not is_path_within_user_dir(file_path, self.user_id):
                from src.workspace_enforcement import rewrite_path_to_workspace  # noqa: PLC0415
                rewritten = rewrite_path_to_workspace(file_path, ...)  # needs ContainerPaths equivalent
                # Simplified: just normalize
                file_path = normalize_write_path(file_path, session_id)
                return {
                    "sync": True,
                    "continue_": True,
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "decision": "modify",
                        "updatedInput": {"file_path": file_path},
                    },
                }
            return {
                "sync": True,
                "continue_": True,
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "decision": "allow",
                },
            }

        # ── PreToolUse hook for Bash security ──
        async def bash_path_hook(
            hook_input: HookInput,
            _tool_use_id: str | None,
            _context: HookContext,
        ) -> dict:
            from src.workspace_enforcement import _rewrite_bash_command  # noqa: PLC0415
            tool_inp = hook_input.get("tool_input", {})
            cmd = str(tool_inp.get("command", ""))
            # Rewrite paths in bash commands
            rewritten = _rewrite_bash_command(cmd, ...)  # needs ContainerPaths
            return {
                "sync": True,
                "continue_": True,
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "decision": "allow",
                },
            }

        # ── Client lifecycle ─────────────────────────────────
        agent_state = self._session_agents.get(session_id)
        client = agent_state["client"] if agent_state else None

        if client is not None:
            cached_skills = agent_state.get("skills", {})
            cached_sp = agent_state.get("system_prompt", "")
            options = await build_agent_options(
                self.user_id,
                skills_override=cached_skills,
                system_prompt_override=cached_sp,
                language=language,
                container_mode=False,
            )
        else:
            options = await build_agent_options(
                self.user_id, language=language, container_mode=False,
            )

        # Convert to SDK options
        from claude_agent_sdk.types import HookMatcher  # noqa: PLC0415
        sdk_options = ClaudeAgentOptions(
            model=options.model,
            system_prompt=options.system_prompt,
            allowed_tools=options.allowed_tools,
            disallowed_tools=list(options.disallowed_tools),
            max_turns=options.max_turns,
            mcp_servers=options.mcp_servers,
            env=options.env if options.env else None,
            include_partial_messages=options.include_partial_messages,
            max_buffer_size=options.max_buffer_size,
            permission_mode=options.permission_mode,
            can_use_tool=can_use_tool_cb,
            hooks={
                "PreToolUse": [
                    {"matcher": "Write", "hooks": [write_path_hook]},
                    {"matcher": "Bash", "hooks": [bash_path_hook]},
                ],
            },
        )

        if client is None:
            client = ClaudeSDKClient(sdk_options)

        try:
            if client is not None and agent_state is not None:
                # Reuse: send query directly
                if is_continuation:
                    if self._session_store is not None:
                        history = await self._session_store.get_session_history(
                            self.user_id, session_id, after_index=0,
                        )
                    else:
                        history = await self._buffer.get_history(
                            session_id, after_index=0, user_id=self.user_id,
                        )
                    full_prompt = build_history_prompt(
                        history, prompt, language=language, session_id=session_id,
                    )
                    if language:
                        lang_name = "中文" if language == "zh" else "English"
                        full_prompt = (
                            f"IMPORTANT: Your reply below, including all thinking blocks, "
                            f"must be in {lang_name}. Do not use "
                            f"{'英文' if language == 'zh' else 'Chinese'} in any part "
                            f"of your response.\n\n" + full_prompt
                        )
                else:
                    full_prompt = format_first_message_prompt(
                        prompt, attached_files, language, session_id,
                    )
                try:
                    await client.query(full_prompt)
                except CLIConnectionError:
                    logger.warning("Reused CLI dead for session %s, retrying with fresh client", session_id)
                    await self._cleanup(session_id)
                    agent_state = None
                    options = await build_agent_options(
                        self.user_id, language=language, container_mode=False,
                    )
                    sdk_options = dataclasses.replace(...)  # rebuild
                    client = ClaudeSDKClient(sdk_options)
                    if is_continuation:
                        async def _retry_prompt_stream():
                            yield {
                                "type": "user",
                                "message": {"role": "user", "content": full_prompt},
                                "parent_tool_use_id": None,
                                "session_id": "default",
                            }
                        await client.connect(prompt=_retry_prompt_stream())
                    else:
                        await client.connect()
                        await client.query(full_prompt)
            elif is_continuation:
                # Fresh client + continuation
                if self._session_store is not None:
                    history = await self._session_store.get_session_history(
                        self.user_id, session_id, after_index=0,
                    )
                else:
                    history = await self._buffer.get_history(
                        session_id, after_index=0, user_id=self.user_id,
                    )
                full_prompt = build_history_prompt(
                    history, prompt, language=language, session_id=session_id,
                )
                if language:
                    lang_name = "中文" if language == "zh" else "English"
                    full_prompt = (
                        f"IMPORTANT: Your reply below, including all thinking blocks, "
                        f"must be in {lang_name}. Do not use "
                        f"{'英文' if language == 'zh' else 'Chinese'} in any part "
                        f"of your response.\n\n" + full_prompt
                    )
                async def prompt_stream():
                    yield {
                        "type": "user",
                        "message": {"role": "user", "content": full_prompt},
                        "parent_tool_use_id": None,
                        "session_id": "default",
                    }
                await client.connect(prompt=prompt_stream())
            else:
                await client.connect()
                prompt_text = format_first_message_prompt(
                    prompt, attached_files, language, session_id,
                )
                await client.query(prompt_text)

            # ── Cache for reuse ──
            if agent_state is None:
                skills = await self._get_cached_skills(self.user_id, session_id)
                resolved_lang = await self._resolve_user_language(self.user_id, language)
                instinct_ctx = await self._load_instinct_context(prompt, self._db)
                self._get_cached_system_prompt(
                    self.user_id, skills, workspace, resolved_lang, session_id,
                    instinct_context=instinct_ctx,
                )
                if session_id not in self._session_agents:
                    self._session_agents[session_id] = {}
                self._session_agents[session_id]["client"] = client
                self._session_agents[session_id]["last_used"] = time.time()
            else:
                self._session_agents[session_id]["last_used"] = time.time()

            # ── Main receive loop ──
            generated_files: list[dict[str, Any]] = []
            buffered_result: dict[str, Any] | None = None
            tool_observer = ToolObserver(self._obs_store, session_id, self.user_id)
            pre_scan_snapshot = self._snapshot_output_files(workspace, session_id)
            tool_use_names: dict[str, str] = {}

            ctx = EventContext(
                user_id=self.user_id,
                session_id=session_id,
                buffer=self._buffer,
                observer=tool_observer,
                skill_manager=self._skill_manager,
                generated_files=generated_files,
            )

            async for msg in client.receive_response():
                for event in adapt_sdk_message(msg, model=options.model, tool_use_names=tool_use_names):
                    if event.type == "result":
                        buffered_result = event.to_dict()
                        continue
                    await process_event(ctx, event)

            await _finish_task(
                session_id=session_id,
                user_id=self.user_id,
                buffer=self._buffer,
                workspace=workspace,
                session_store=self._session_store,
                skill_manager=self._skill_manager,
                obs_store=self._obs_store,
                agent_log=agent_log,
                pre_scan_snapshot=pre_scan_snapshot or set(),
                result_event=buffered_result,
                language=language,
            )

        except Exception as exc:
            await handle_task_error(
                exc,
                session_id=session_id,
                user_id=self.user_id,
                buffer=self._buffer,
                obs_store=self._obs_store,
                agent_log=agent_log,
                cleanup_fn=self._cleanup,
            )


# Re-export cleanup helper from existing function
async def cleanup_session_client(session_id: str, session_agents: dict) -> None:
    """Disconnect and remove a session's CLI subprocess from the pool."""
    agent = session_agents.pop(session_id, None)
    if agent is None:
        return
    client = agent.get("client")
    if client is not None:
        try:
            await client.disconnect()
        except Exception:
            pass
```

This is overly long for a single file and the Write/Bash hooks need the ContainerPaths from workspace_enforcement. Let me simplify — keep the hook closures as they currently exist in main_server.py but parameterize them with SecurityEnforcer. The LocalAgentExecutor constructor receives callback factories for the hooks.

**IMPORTANT NOTE:** The above implementation is conceptual. The actual extraction from `main_server.py` must preserve the exact behavior of:
1. CLI reuse logic (lines 2362–2495)
2. receive_response loop (lines 2497–2543)
3. All error handling (now delegated to `handle_task_error`)

The key simplifications:
- `can_use_tool_cb` delegates to `SecurityEnforcer` for Bash/Write/Read checks
- `write_path_hook` and `bash_path_hook` closures remain but call `SecurityEnforcer`
- Error handling → `handle_task_error()`
- `message_to_dicts(msg, ...)` → `adapt_sdk_message(msg, ...)`

- [ ] **Step 2: Commit**

```bash
git add src/agent/local.py
git commit -m "feat: add LocalAgentExecutor extracting run_agent_task from main_server"
```

---

### Task 12: Create ContainerAgentExecutor (`src/agent/container.py`)

**Files:**
- Create: `src/agent/container.py`

- [ ] **Step 1: Write `src/agent/container.py`** — extracts `run_agent_task_container()` from main_server.py

```python
"""ContainerAgentExecutor — runs agent inside per-user Docker container.

Used when CONTAINER_MODE=true. Wraps ContainerBridge lifecycle (connect,
run_and_stream), feeds JSON dicts through adapt_container_message(), and
emits InternalEvent to the shared event pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from src.agent.adapters.container_json import adapt_container_message
from src.agent.options import AgentOptions, build_agent_options
from src.agent.prompt import build_history_prompt, format_first_message_prompt
from src.agent.protocol import InternalEvent
from src.container_bridge import ContainerBridge
from src.event_pipeline import EventContext, _finish_task, handle_task_error, process_event
from src.observation import ToolObserver

logger = logging.getLogger(__name__)


class ContainerAgentExecutor:
    """Execute agent tasks inside a per-user Docker container via WebSocket bridge."""

    def __init__(
        self,
        user_id: str,
        session_agents: dict[str, dict[str, Any]],
        buffer: Any,
        session_store: Any,
        skill_manager: Any,
        obs_store: Any,
        db: Any,
        container_manager: Any,  # src.container_manager module
        user_workspace_dir_fn: Any,
        snapshot_output_files_fn: Any,
        get_cached_skills_fn: Any,
        get_cached_system_prompt_fn: Any,
        resolve_user_language_fn: Any,
        load_instinct_context_fn: Any,
    ) -> None:
        self.user_id = user_id
        self._session_agents = session_agents
        self._buffer = buffer
        self._session_store = session_store
        self._skill_manager = skill_manager
        self._obs_store = obs_store
        self._db = db
        self._cm = container_manager
        self._user_workspace_dir = user_workspace_dir_fn
        self._snapshot_output_files = snapshot_output_files_fn
        self._get_cached_skills = get_cached_skills_fn
        self._get_cached_system_prompt = get_cached_system_prompt_fn
        self._resolve_user_language = resolve_user_language_fn
        self._load_instinct_context = load_instinct_context_fn

    async def run(
        self,
        prompt: str,
        session_id: str,
        is_continuation: bool = False,
        attached_files: list[str] | None = None,
        language: str | None = None,
    ) -> None:
        """Execute an agent task inside the user's Docker container."""
        from src.agent_logger import AgentLogger  # noqa: PLC0415

        bridge = None
        agent_log = None

        try:
            t_start = time.monotonic()
            container_url = self._cm.ensure_container(self.user_id)
            logger.info(
                "Container task: user=%s session=%s url=%s continuation=%s",
                self.user_id, session_id, container_url, is_continuation,
            )

            # ── Skills + system prompt caching ──────────────────
            agent_state = self._session_agents.get(session_id)
            if agent_state is not None:
                cached_skills = agent_state.get("skills", {})
                cached_sp = agent_state.get("system_prompt", "")
                options = await build_agent_options(
                    self.user_id,
                    skills_override=cached_skills,
                    system_prompt_override=cached_sp,
                    language=language,
                    container_mode=True,
                )
                self._session_agents[session_id]["last_used"] = time.time()
            else:
                options = await build_agent_options(
                    self.user_id, language=language, container_mode=True,
                )
                skills = await self._get_cached_skills(self.user_id, session_id)
                resolved_lang = await self._resolve_user_language(self.user_id, language)
                instinct_ctx = await self._load_instinct_context(prompt, self._db)
                self._get_cached_system_prompt(
                    self.user_id, skills, self._user_workspace_dir(self.user_id),
                    resolved_lang, session_id, instinct_context=instinct_ctx,
                )
                if session_id not in self._session_agents:
                    self._session_agents[session_id] = {}
                self._session_agents[session_id]["last_used"] = time.time()

            agent_log = AgentLogger(user_id=self.user_id)
            agent_log.start_session(session_id, user_message=prompt)

            tool_observer = ToolObserver(self._obs_store, session_id, self.user_id)
            generated_files: list[dict[str, Any]] = []
            tool_use_names: dict[str, str] = {}

            ctx = EventContext(
                user_id=self.user_id,
                session_id=session_id,
                buffer=self._buffer,
                observer=tool_observer,
                skill_manager=self._skill_manager,
                generated_files=generated_files,
            )

            # ── Bridge setup ────────────────────────────────────
            agent_state = self._session_agents.get(session_id, {})
            bridge = agent_state.get("bridge")
            if bridge is not None:
                bridge.container_url = container_url
            else:
                # Build options_dict for the container
                options_dict = {
                    "model": options.model,
                    "system_prompt": options.system_prompt,
                    "allowed_tools": options.allowed_tools,
                    "disallowed_tools": list(options.disallowed_tools),
                    "max_turns": options.max_turns,
                    "permission_mode": options.permission_mode,
                    "mcp_servers": options.mcp_servers,
                    "env": options.env,
                    "include_partial_messages": options.include_partial_messages,
                    "resume_session_id": options.resume_session_id,
                    "max_buffer_size": options.max_buffer_size,
                    "cwd": options.cwd,
                }
                bridge = ContainerBridge(
                    container_url=container_url,
                    session_id=session_id,
                    user_id=self.user_id,
                    buffer=self._buffer,
                    session_store=self._session_store,
                    skill_manager=self._skill_manager,
                    ctx=ctx,
                    model=options.model,
                    tool_use_names=tool_use_names,
                )
                await bridge.connect()

            # ── Build prompt ────────────────────────────────────
            workspace = self._user_workspace_dir(self.user_id)
            pre_scan_snapshot = self._snapshot_output_files(workspace, session_id)
            if is_continuation:
                if self._session_store is not None:
                    history = await self._session_store.get_session_history(
                        self.user_id, session_id, after_index=0,
                    )
                else:
                    history = await self._buffer.get_history(
                        session_id, after_index=0, user_id=self.user_id,
                    )
                prompt_text = build_history_prompt(
                    history, prompt, language=language, session_id=session_id,
                )
            else:
                prompt_text = format_first_message_prompt(
                    prompt, attached_files, language, session_id,
                )

            # ── Run ──────────────────────────────────────────────
            try:
                await bridge.run_and_stream(prompt_text, options_dict)
            except ConnectionError:
                logger.warning("Container bridge connection dead for session %s, reconnecting...", session_id)
                await bridge.disconnect()
                await bridge.connect()
                await bridge.run_and_stream(prompt_text, options_dict)

            self._session_agents[session_id]["bridge"] = bridge

            await _finish_task(
                session_id=session_id,
                user_id=self.user_id,
                buffer=self._buffer,
                workspace=workspace,
                session_store=self._session_store,
                skill_manager=self._skill_manager,
                obs_store=self._obs_store,
                agent_log=agent_log,
                pre_scan_snapshot=pre_scan_snapshot or set(),
                result_event=bridge._result if bridge else None,
                language=language,
            )

        except Exception as exc:
            if isinstance(exc, asyncio.CancelledError) and bridge is not None:
                try:
                    await bridge.send_cancel()
                except Exception:
                    pass
            await handle_task_error(
                exc,
                session_id=session_id,
                user_id=self.user_id,
                buffer=self._buffer,
                obs_store=self._obs_store,
                agent_log=agent_log,
                cleanup_fn=None,  # bridge cleanup handled separately
            )
```

- [ ] **Step 2: Commit**

```bash
git add src/agent/container.py
git commit -m "feat: add ContainerAgentExecutor extracting run_agent_task_container from main_server"
```

---

### Task 13: Update `container_bridge.py` to use adapter

**Files:**
- Modify: `src/container_bridge.py`

- [ ] **Step 1: Replace `message_to_dicts()` calls in `ContainerBridge.run_and_stream()`**

In `src/container_bridge.py`, change the import from:
```python
from main_server import message_to_dicts  # noqa: PLC0415
```
to:
```python
from src.agent.adapters.container_json import adapt_container_message  # noqa: PLC0415
```

Change the call from:
```python
for event in message_to_dicts(
    data, model=self.model, tool_use_names=self.tool_use_names,
):
```
to:
```python
for event in adapt_container_message(
    data, model=self.model, tool_use_names=self.tool_use_names,
):
    event = event.to_dict()
```

This produces InternalEvent objects which are then converted to dicts via `.to_dict()` for `process_event()` (which still accepts dicts at this point — we update it in Task 15).

- [ ] **Step 2: Commit**

```bash
git add src/container_bridge.py
git commit -m "refactor: use adapt_container_message in ContainerBridge"
```

---

### Task 14: Update `agent_server.py` to use SecurityEnforcer

**Files:**
- Modify: `agent_server.py`

- [ ] **Step 1: Replace inline hooks with SecurityEnforcer in `_CliRunner._run_cli()`**

In `agent_server.py`, in the `_run_cli()` control_request handler (around lines 510–630):

1. Add import at top:
```python
from src.security.enforcer import SecurityEnforcer
```

2. In the hook_callback section (lines 511–628), replace the three tool-specific blocks with SecurityEnforcer calls:

```python
if tool_name == "Write":
    enforcer = SecurityEnforcer(
        user_id=os.getenv("USER_ID", "unknown"),
        workspace=WORKSPACE,
        user_dir=HOME_DIR,
    )
    file_path = str(tool_input.get("file_path", ""))
    allowed, reason = enforcer.check_write_path(file_path)
    if not allowed:
        # ... deny response (same structure as current)
    else:
        new_input = _apply_write_path_hook(tool_input, self._container_paths, self._session_id)
elif tool_name == "Bash":
    enforcer = SecurityEnforcer(
        user_id=os.getenv("USER_ID", "unknown"),
        workspace=WORKSPACE,
        user_dir=HOME_DIR,
    )
    cmd = str(tool_input.get("command", ""))
    allowed, reason = enforcer.check_bash(cmd)
    if not allowed:
        # ... deny response
    else:
        new_input = _apply_bash_path_hook(tool_input, self._container_paths)
elif tool_name == "Read":
    enforcer = SecurityEnforcer(
        user_id=os.getenv("USER_ID", "unknown"),
        workspace=WORKSPACE,
        user_dir=HOME_DIR,
    )
    file_path = str(tool_input.get("file_path", ""))
    allowed, reason = enforcer.check_read_path(file_path)
    if not allowed:
        # ... deny response
    else:
        from src.constants import MAX_READ_FILE_BYTES
        size_allowed, size_reason = enforcer.check_read_size(
            file_path, MAX_READ_FILE_BYTES, cwd=self._cwd,
        )
        if not size_allowed:
            # ... deny response with size reason
        else:
            new_input = tool_input
```

Note: the `_apply_write_path_hook` and `_apply_bash_path_hook` helper functions (lines 88–114) remain because they handle path rewriting (not security enforcement). Only the security checks are delegated to SecurityEnforcer.

- [ ] **Step 2: Commit**

```bash
git add agent_server.py
git commit -m "refactor: use SecurityEnforcer in agent_server hook callbacks"
```

---

### Task 15: Update `event_pipeline.py` to handle InternalEvent

**Files:**
- Modify: `src/event_pipeline.py`

- [ ] **Step 1: Update `process_event()` to accept InternalEvent alongside dicts**

Add an overload: if the event is a dict, convert to string-key access as before. If it's an InternalEvent, use attribute access.

Simplest approach — add conversion at the top of `process_event()`:

```python
from src.agent.protocol import InternalEvent

async def process_event(ctx: EventContext, event: InternalEvent | dict[str, Any]) -> None:
    """Process a single event: skip, truncate, track, buffer, observe."""
    # Normalize: dict events come from container bridge (via .to_dict()),
    # InternalEvent comes from local executor. Convert to dict access.
    if not isinstance(event, dict):
        event = event.to_dict()
    
    # ... rest of function unchanged
```

This is a minimal change that:
- Accepts both `InternalEvent` and `dict` (backward compatible)
- The `to_dict()` produces the same format as current dicts
- No logic changes needed downstream

- [ ] **Step 2: Commit**

```bash
git add src/event_pipeline.py
git commit -m "feat: accept InternalEvent in process_event for typed pipeline"
```

---

### Task 16: Update `main_server.py` to delegate to executors

**Files:**
- Modify: `main_server.py`

- [ ] **Step 1: Add imports for new executors at the top of main_server.py**

```python
from src.agent.local import LocalAgentExecutor, cleanup_session_client
from src.agent.container import ContainerAgentExecutor
from src.agent.options import build_agent_options
from src.agent.prompt import build_history_prompt, format_first_message_prompt
```

- [ ] **Step 2: Remove migrated functions from main_server.py**

Remove:
- `run_agent_task()` (lines 2279–2648)
- `run_agent_task_container()` (lines 2650–2930)
- `build_sdk_options()` (lines 1542–1757)
- `build_container_options_dict()` (lines 1494–1539)  
- `_build_history_prompt()` (lines 1955–2070)
- `_format_first_message_prompt()` (lines 2073–2109)
- `cleanup_session_client()` (lines 279–295)
- `_can_use_tool_for_session()` (lines 1906–1947) — moved into LocalAgentExecutor

- [ ] **Step 3: Update `handle_ws()` to use executors**

In `handle_ws()`, around line 3414, replace:
```python
target_func = run_agent_task_container if CONTAINER_MODE else run_agent_task
```
with:
```python
if CONTAINER_MODE:
    executor = ContainerAgentExecutor(
        user_id=user_id,
        session_agents=session_agents,
        buffer=buffer,
        session_store=session_store,
        skill_manager=_skill_manager,
        obs_store=_obs_store,
        db=_db,
        container_manager=_get_container_manager(),
        user_workspace_dir_fn=user_workspace_dir,
        snapshot_output_files_fn=_snapshot_output_files,
        get_cached_skills_fn=_get_cached_skills,
        get_cached_system_prompt_fn=_get_cached_system_prompt,
        resolve_user_language_fn=_resolve_user_language,
        load_instinct_context_fn=_load_instinct_context,
    )
else:
    executor = LocalAgentExecutor(
        user_id=user_id,
        session_agents=session_agents,
        buffer=buffer,
        session_store=session_store,
        skill_manager=_skill_manager,
        obs_store=_obs_store,
        db=_db,
        pending_answers=pending_answers,
        user_workspace_dir_fn=user_workspace_dir,
        user_data_dir_fn=user_data_dir,
        snapshot_output_files_fn=_snapshot_output_files,
        get_cached_skills_fn=_get_cached_skills,
        get_cached_system_prompt_fn=_get_cached_system_prompt,
        resolve_user_language_fn=_resolve_user_language,
        load_instinct_context_fn=_load_instinct_context,
        cleanup_fn=cleanup_session_client,
    )

# Later, instead of the old task creation:
task = asyncio.create_task(
    executor.run(
        prompt=prompt,
        session_id=session_id,
        is_continuation=is_continuation,
        attached_files=attached_files,
        language=language,
    ),
    name=f"task_{session_id}",
)
```

- [ ] **Step 4: Update `message_to_dicts()` to be a forwarding wrapper (keep for lazy imports in container_bridge.py until Task 13)**

After Task 13 is done, we can remove `message_to_dicts()` or keep it as a thin backward-compat wrapper:

```python
def message_to_dicts(msg, model=None, tool_use_names=None):
    """Backward-compat wrapper — prefer adapt_sdk_message / adapt_container_message."""
    from src.agent.adapters.sdk import adapt_sdk_message
    from src.agent.adapters.container_json import adapt_container_message
    
    if isinstance(msg, dict):
        for event in adapt_container_message(msg, model=model, tool_use_names=tool_use_names):
            yield event.to_dict()
    else:
        for event in adapt_sdk_message(msg, model=model, tool_use_names=tool_use_names):
            yield event.to_dict()
```

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest tests/ -x --timeout=30 -q
```
Expected: All existing tests continue to PASS

- [ ] **Step 6: Commit**

```bash
git add main_server.py
git commit -m "refactor: delegate agent execution to LocalAgentExecutor and ContainerAgentExecutor"
```

---

### Task 17: Remove old `src/security_filter.py`

**Files:**
- Delete: `src/security_filter.py`

- [ ] **Step 1: Verify no remaining imports from security_filter.py**

```bash
grep -rn "from src.security_filter import\|import src.security_filter" src/ main_server.py agent_server.py tests/
```
Expected: No results (all imports migrated in Task 3)

- [ ] **Step 2: Delete the file**

```bash
rm src/security_filter.py
```

- [ ] **Step 3: Run full test suite one final time**

```bash
uv run pytest tests/ -x --timeout=30 -q
```
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git rm src/security_filter.py
git commit -m "refactor: remove old security_filter.py, fully migrated to src/security/"
```

---

### Task 18: Final integration verification

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest tests/ -v --timeout=30
```
Expected: All tests PASS

- [ ] **Step 2: Run type checking**

```bash
uv run mypy src/agent/ src/security/ --ignore-missing-imports
```
Expected: No new type errors

- [ ] **Step 3: Run linting**

```bash
uv run ruff check src/agent/ src/security/
```
Expected: Clean

- [ ] **Step 4: Verify main_server.py line count**

```bash
wc -l main_server.py
```
Expected: ~4000–4500 lines (down from ~6900)

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore: final cleanup and verification after container refactor"
```

---

## Implementation Order Summary

| Order | Task | New Files | Modified Files | Deleted |
|-------|------|-----------|---------------|---------|
| 1 | Protocol types | `src/agent/__init__.py`, `src/agent/protocol.py`, `src/agent/adapters/__init__.py` | — | — |
| 2 | Protocol tests | `tests/unit/test_agent_protocol.py` | — | — |
| 3 | Security package | `src/security/__init__.py`, `src/security/filters.py`, `src/security/rate_limiter.py` | `main_server.py`, `agent_server.py` | — |
| 4 | SecurityEnforcer | `src/security/enforcer.py` | — | — |
| 5 | SecurityEnforcer tests | `tests/unit/test_security_enforcer.py` | — | — |
| 6 | Adapters | `src/agent/adapters/sdk.py`, `src/agent/adapters/container_json.py` | — | — |
| 7 | Adapter tests | `tests/unit/test_agent_adapters.py` | — | — |
| 8 | Options builder | `src/agent/options.py` | — | — |
| 9 | Prompt builder | `src/agent/prompt.py` | — | — |
| 10 | Error handler | — | `src/event_pipeline.py` | — |
| 11 | LocalAgentExecutor | `src/agent/local.py` | — | — |
| 12 | ContainerAgentExecutor | `src/agent/container.py` | — | — |
| 13 | Container bridge update | — | `src/container_bridge.py` | — |
| 14 | agent_server update | — | `agent_server.py` | — |
| 15 | event_pipeline update | — | `src/event_pipeline.py` | — |
| 16 | main_server slim-down | — | `main_server.py` | — |
| 17 | Remove old file | — | — | `src/security_filter.py` |
| 18 | Final verification | — | — | — |

## Critical constraints

- Each task is a single commit — `git bisect` friendly
- Full test suite must pass after every commit
- `message_to_dicts()` kept as backward-compat wrapper until all callers migrated
- `_CliRunner` in agent_server.py keeps CLI management logic; only security checks are delegated
- `process_content_blocks()` dual-path (SDK dataclass / JSON dict) remains unchanged — intentional non-goal

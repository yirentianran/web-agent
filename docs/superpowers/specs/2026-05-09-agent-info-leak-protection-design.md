---
name: Agent Information Leakage Protection
description: Hard control design for preventing agent from leaking sensitive information (env vars, system details, architecture) to users via output filtering and tool-level interception
type: design
---

# Agent Information Leakage Protection — Design

## Problem

Currently the web-agent relies on **soft controls** (system prompt instructions) to prevent the agent from leaking sensitive information. These can be bypassed:

- Agent can use `Bash` tool to run `env`, `uname -a`, `cat .env`
- Agent can use `Read` tool to read source code, config files
- Agent can output sensitive content in conversation freely
- No server-side output filtering exists

## Scope

Two attack vectors:
1. **User social engineering** — user asks agent to reveal system info
2. **Accidental leakage** — agent outputs sensitive info in error logs, debug output, tool results

Boundary: Agent **can** use sensitive info internally (e.g., API keys for model calls), but **must never** output them to the user.

## Architecture

### Three Layers of Defense

```
Layer 1: System Prompt (existing, soft control)
         └── Instructions in system prompt about what not to disclose

Layer 2: PreToolUse Hooks (new, hard control)
         ├── BashCommandFilter.check(cmd) → (allowed, reason)
         └── FileAccessFilter.check(path) → (allowed, reason)

Layer 3: Output Filter (new, hard control)
         └── OutputFilter.scan(text) → sanitized text
```

### New Module: `src/security_filter.py`

```python
class OutputFilter:
    """Scan agent output text and replace sensitive content."""
    PATTERNS = [
        # API keys: sk-..., anth-..., etc.
        r'(?:sk|anth|openai)[\-_][a-zA-Z0-9]{20,}',
        # Env var assignments with sensitive names
        r'(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH)[=:]\S+',
        # Internal paths
        r'/Users/mac/Documents/Projects/web-agent[^\s]*',
        # Container/infrastructure identifiers
        r'(?i)(?:container_id|hostname|instance_id)[=:]\S+',
    ]
    BLOCK_PATTERNS = [
        # Full-text blockers (replace entire block)
        r'uname\s+-[aA]',
        r'/etc/(?:passwd|shadow|hosts)',
    ]

    @classmethod
    def scan(cls, text: str) -> str: ...

class BashCommandFilter:
    """Pre-execute check for dangerous bash commands."""
    DENY_LIST = {
        "env", "printenv", "compgen -v", "set", "export -p",
        "uname", "hostname", "whoami", "id",
    }
    SENSITIVE_FILES = {".env", ".env.*", "credentials.json", "*.pem", "*.key"}

    @classmethod
    def check(cls, command: str) -> tuple[bool, str]: ...

class FileAccessFilter:
    """Pre-execute check for sensitive file reads."""
    DENY_PATTERNS = [
        r'\.env(\.\w+)?$',
        r'credentials\.\w+$',
        r'\.(pem|key)$',
        r'/etc/(passwd|shadow|hosts)$',
    ]

    @classmethod
    def check(cls, path: str) -> tuple[bool, str]: ...
```

## Data Flow

```
Assistant text → OutputFilter.scan() → WebSocket → User
Bash tool use  → BashCommandFilter.check() → reject/pass → execute
Read tool use  → FileAccessFilter.check() → reject/pass → execute
```

Filtering applies to agent→user direction only. Agent internal context is unchanged.

## Integration Points

| File | Change |
|------|--------|
| `src/security_filter.py` | New module: three filter classes |
| `main_server.py` event loop | Call `OutputFilter.scan()` on assistant text before forwarding |
| `main_server.py` hooks | Add Bash and Read PreToolUse hooks |
| `agent_server.py` | Same OutputFilter on CLI stdout events; same hooks |
| `tests/` | Unit tests for each filter class + integration tests |

## Security Filter Behavior

### OutputFilter.scan()
- For each PATTERNS match: replace value with `*** (hidden)***`
- For each BLOCK_PATTERNS match: replace entire block with `[Content blocked: system information detected]`
- Runs on every `assistant` message content and `tool_result` content before forwarding to user
- Designed for low latency (pre-compiled regex, single pass)

### BashCommandFilter.check()
- Returns `(False, "reason")` for denied commands
- Returns `(True, "")` for allowed commands
- Uses regex to extract the base command from complex commands
- Distinguishes `export VAR=value` (allowed, sets variable) from `export -p` (denied, lists all)
- Applied as PreToolUse hook, returning a rejection to the agent

### FileAccessFilter.check()
- Checks file paths against sensitive patterns
- Works for both absolute and relative paths
- Applied to `Read` tool's `file_path` parameter

## Error Handling

- Filter failures default to **deny** (fail-closed)
- Rejection messages are generic: "This operation is not permitted for security reasons."
- No details about what was blocked (to avoid information leakage through error messages)
- Logged internally at DEBUG level for admin review

## Testing

- Unit tests: pattern matching, edge cases, false positives
- Integration tests: simulated agent output with embedded secrets
- Performance test: filter latency on typical message sizes (<1ms per scan)

## Future (Out of Scope)

- Environment variable isolation (C layer) — agent process doesn't hold secrets
- Per-user secret scoping — different users see different filtered values
- Rate limiting on information-seeking patterns

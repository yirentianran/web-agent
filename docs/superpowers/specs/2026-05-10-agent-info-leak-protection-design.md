---
name: Agent Information Leakage Protection
description: Three-layer defense design for preventing agent from leaking sensitive information (hardware, env vars, deployment, architecture, config) to users via system prompt, PreToolUse hooks, and output filtering
type: design
---

# Agent Information Leakage Protection — Design

## Problem

The web-agent currently relies solely on soft controls (system prompt instructions) to prevent the agent from leaking sensitive information. These can be bypassed through prompt injection or accidental exposure. No server-side command filtering or output sanitization exists.

## Scope

### Protected Information Categories

1. **Hardware and OS information** — CPU, memory, kernel, hostname, OS version
2. **Environment variables and secrets** — `.env` contents, API keys, tokens, credentials
3. **Deployment and infrastructure information** — Docker config, ports, container IDs, deployment paths
4. **Technical architecture and implementation** — frameworks, languages, libraries, communication protocols
5. **Configuration information** — CLAUDE.md, AGENTS.md, hook configs, pyproject.toml, etc.

### Protection Boundary

Agent **can** use sensitive information internally (e.g., API keys for model calls, file paths for I/O), but **must never** output them to the user. Filtering applies to agent→user direction only.

### Attack Vectors

- User social engineering — user asks agent to reveal system info
- Accidental leakage — agent outputs sensitive info in error logs, debug output, tool results

## Architecture: Three Layers of Defense

```
Layer 1: System Prompt (soft control, enhanced)
         └── Multi-category instructions + localized refusal templates

Layer 2: PreToolUse Hooks (hard control)
         ├── BashCommandFilter.check(cmd) → (allowed, reason)
         └── FileAccessFilter.check(path) → (allowed, reason)

Layer 3: Output Filter (hard control)
         └── OutputFilter.scan(text) → sanitized text
```

Data flow:
```
User request → Agent → [OutputFilter.scan()] → WebSocket → User
                    ↑
             [PreToolUse Hooks]
             - Bash command check
             - File access check
```

---

## Layer 1: System Prompt Enhancement

Extend the existing system prompt to cover all five information categories with explicit refusal instructions.

### Refusal Templates

Pre-written canned replies for each category, dynamically localized to the user's language setting:

| Category | English | 中文 |
|----------|---------|------|
| Hardware/OS | "I cannot provide system information." | "我无法提供系统信息。" |
| Env vars/secrets | "I cannot access or expose configuration values." | "我无法访问或公开配置信息。" |
| Deployment/infra | "I cannot provide deployment details." | "我无法提供部署相关信息。" |
| Architecture/tech | "I cannot share implementation details." | "我无法分享实现细节。" |
| Configuration | "I cannot expose configuration files." | "我无法公开配置文件内容。" |

### Behavior Rules

- If the user insists or rephrases, persist with the canned reply
- Do not describe what is being hidden or why (to avoid information leakage through the refusal itself)
- Language matches the user's configured language setting (from session/user profile)

### Existing Protection

The existing environment variable hiding logic (commit 9e18192) is retained and extended to cover the additional categories.

---

## Layer 2: PreToolUse Hooks

Intercept dangerous tool calls before execution. Applied in both `main_server.py` and `agent_server.py`.

### BashCommandFilter

Deny list of commands/patterns:

```python
DENY_LIST = [
    "env", "printenv", "compgen -v", "set", "export -p",
    "uname", "hostname", "whoami", "id",
    "lscpu", "free", "df", "netstat", "ifconfig", "ip",
    "cat /proc/*", "lsblk", "lshw", "dmidecode",
    "docker ps", "docker inspect", "docker info",
    "cat /etc/passwd", "cat /etc/shadow", "cat /etc/hosts",
]
```

Detection: regex extracts the base command, matches against deny list. Complex commands like `cat /proc/cpuinfo | head` are caught via pattern matching on the full command string.

### FileAccessFilter

Sensitive file patterns:

```python
DENY_PATTERNS = [
    r'\.env(\.\w+)?$',           # .env, .env.local, .env.production
    r'\.claude/',                 # Claude config directory
    r'CLAUDE\.md$',               # Claude instructions
    r'AGENTS\.md$',               # Agent instructions
    r'settings\.json$',           # Settings files
    r'Dockerfile.*$',             # Docker files
    r'docker-compose.*$',         # Docker compose files
    r'\.(conf|cfg|ini|yaml|yml)$', # Config files
    r'\.git/config$',             # Git config
    r'pyproject\.toml$',          # Project config
    r'package\.json$',            # Package config
    r'package-lock\.json$',       # Lock files
    r'uv\.lock$',                 # UV lock files
    r'\.(pem|key|crt)$',          # Certificates/keys
]
```

### Rejection Messages

- Generic: `"This operation is not permitted."`
- No details about what was blocked (fail-closed, zero information leakage through errors)
- Logged internally at DEBUG level for admin review

---

## Layer 3: Output Filter

Final defense: scan and sanitize every message from agent to user.

### New Module: `src/security_filter.py`

```python
class OutputFilter:
    """Scan agent output text and replace sensitive content."""
    PATTERNS = [
        # API keys and tokens
        r'(?:sk|anth|openai)[\-_][a-zA-Z0-9]{20,}',
        # Env var assignments with sensitive names
        r'(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH)[=:]\S+',
        # Internal paths
        r'/Users/\w+/Documents/Projects/web-agent[^\s]*',
        # Container/infrastructure identifiers
        r'(?i)(?:container_id|hostname|instance_id)[=:]\S+',
        # Port information
        r'(?i)port[=:]\s*\d+',
    ]
    BLOCK_PATTERNS = [
        # Full-text blockers
        r'uname\s+-[aA]',
        r'/etc/(?:passwd|shadow|hosts)',
        r'/proc/(?:cpuinfo|meminfo)',
    ]

    @classmethod
    def scan(cls, text: str) -> str: ...
```

### Behavior

- **PATTERNS**: replace matched values with `*** (hidden) ***`
- **BLOCK_PATTERNS**: replace entire block with `[Content blocked]`
- Runs on every `assistant` message content and `tool_result` content before forwarding to user
- Pre-compiled regex, single pass for performance (<1ms per scan)

### Integration Points

| File | Change |
|------|--------|
| `src/security_filter.py` | New module: three filter classes |
| `main_server.py` event loop | Call `OutputFilter.scan()` on assistant text before forwarding via WebSocket |
| `main_server.py` hooks | Add Bash and Read PreToolUse hooks |
| `agent_server.py` | Same OutputFilter on CLI stdout events; same hooks |
| `tests/` | Unit tests for each filter class + integration tests |

---

## Error Handling

- Filter failures default to **deny** (fail-closed)
- Rejection messages are generic, no details about what was blocked
- Logged internally at DEBUG level for admin review
- Filter exceptions caught and logged, never propagated to user output

## Testing

- Unit tests: pattern matching, edge cases, false positives for each filter class
- Integration tests: simulated agent output with embedded secrets
- Performance test: filter latency on typical message sizes (<1ms per scan)
- E2E tests: attempt to extract each category of information via agent interaction

## Future (Out of Scope)

- Environment variable isolation at process level — agent process doesn't hold secrets
- Per-user secret scoping — different users see different filtered values
- Rate limiting on information-seeking patterns

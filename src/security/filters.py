"""Output, bash command, and file access filtering for security enforcement."""

from __future__ import annotations

import re
import time
from typing import Final


class OutputFilter:
    """Scan agent output text and replace sensitive content.

    Filters apply to agent-to-user direction only.
    """

    # Value-replacement patterns: replace matched value with *** (hidden) ***
    _PATTERNS: Final[list[tuple[re.Pattern[str], str]]] = [
        # API keys: sk-..., anth-..., openai-...
        (re.compile(r"(?:sk|anth|openai)[\-_][a-zA-Z0-9]{20,}"), "*** (hidden) ***"),
        # Env var assignments: KEY=value, SECRET=value, MODEL=value, etc.
        # Matches uppercase env var names (≥3 chars) with any non-empty value.
        (re.compile(r"\b([A-Z_]{3,}[A-Z0-9])\s*[=:]\s*\S+"), r"\1=*** (hidden) ***"),
        # Internal project paths
        (re.compile(r"/Users/\w+/Documents/Projects/web-agent[^\s]*"), "*** (hidden) ***"),
        # Container/infrastructure identifiers
        (re.compile(r"(?i)(?:container_id|hostname|instance_id)\s*[=:]\s*\S+"), "*** (hidden) ***"),
        # Port information
        (re.compile(r"(?i)\bport[=:\s]+\d+"), "*** (hidden) ***"),
    ]

    # Block patterns: replace entire line/block with [Content blocked]
    _BLOCK_PATTERNS: Final[list[re.Pattern[str]]] = [
        re.compile(r"\buname\b"),
        re.compile(r"/etc/(?:passwd|shadow|hosts)"),
        re.compile(r"/proc/"),
        # Environment variable assignments (env-dump output)
        re.compile(r"^[A-Z_]{3,}[A-Z0-9]\s*[=:]\s*\S+", re.MULTILINE),
        # Markdown table rows with env var names (e.g. | MODEL | value |)
        re.compile(r"^\|\s*[A-Z_]{3,}[A-Z0-9]{0,20}\s*\|"),
    ]

    _BLOCKED_MARKER: Final[str] = "[Content blocked]"
    _HIDDEN_MARKER: Final[str] = "*** (hidden) ***"

    @classmethod
    def scan(cls, text: str) -> str:
        """Scan text and replace sensitive content.

        Returns sanitized text safe to send to user.
        """
        if not text:
            return text

        result = text

        # First: apply block patterns (full-line replacements)
        for pattern in cls._BLOCK_PATTERNS:
            if pattern.search(result):
                lines = result.split("\n")
                blocked_lines: list[str] = []
                for line in lines:
                    if pattern.search(line):
                        blocked_lines.append(cls._BLOCKED_MARKER)
                    else:
                        blocked_lines.append(line)
                result = "\n".join(blocked_lines)

        # Second: apply value-replacement patterns
        for pattern, replacement in cls._PATTERNS:
            result = pattern.sub(replacement, result)

        return result


class BashCommandFilter:
    """Pre-execute check for dangerous bash commands.

    Returns (allowed, reason) tuple.
    If allowed is False, the command must be rejected.
    """

    _DENY_COMMANDS: Final[set[str]] = {
        # System probing
        "env",
        "printenv",
        "compgen",
        "set",
        "export",
        "declare",
        "typeset",
        "uname",
        "hostname",
        "whoami",
        "id",
        "lscpu",
        "free",
        "df",
        "netstat",
        "ifconfig",
        "ip",
        "lsblk",
        "lshw",
        "dmidecode",
        # Network egress
        "curl",
        "wget",
        "nc",
        "ncat",
        "netcat",
        "ssh",
        "scp",
        "sftp",
        "rsync",
        "telnet",
        "ftp",
        "tftp",
        "socat",
        "nmap",
    }

    _DENY_PATTERNS: Final[list[re.Pattern[str]]] = [
        re.compile(r"^\s*cat\s+/proc/"),
        re.compile(r"^\s*docker\s+(ps|inspect|info)\b"),
        re.compile(r"^\s*cat\s+/etc/(?:passwd|shadow|hosts)\b"),
        re.compile(r"^\s*cat\s+\.env"),
        re.compile(r"^\s*(env|printenv)\b"),
        # Env-probing via scripting languages
        re.compile(
            r"""^\s*(python3?|ruby|perl|node)\s+(-[ce]|-c\s|"|')""",
            re.IGNORECASE,
        ),
        re.compile(r"""^\s*(python3?|ruby|perl|node)\s+--.*-[ce]\b""", re.IGNORECASE),
        # os.environ / process.env / ENV / %ENV access via interpreter flags
        re.compile(r"""os\.environ|process\.environ|process\.env\b|["']ENV["']|["']%ENV["']"""),
        # Python HTTP, socket, subprocess network
        re.compile(
            r"""\b(urllib\.request|urllib\.urlopen|requests\.(get|post|put|delete|head|patch)"""
            r"""|httpx\.(get|post)|http\.client|socket\.(socket|connect|create_connection))\b"""
        ),
        # Python subprocess calling external tools
        re.compile(r"""\bsubprocess\.(call|run|Popen|check_output)\b"""),
        # Node.js HTTP / fetch / net
        re.compile(r"""\b(fetch\s*\(|require\s*\(\s*['"](?:https?|net)['"]|\.createConnection\s*\()"""),
        # Ruby Net::HTTP, open-uri, REST clients
        re.compile(r"""\b(Net::HTTP|open-uri|RestClient|Faraday)\b"""),
        # Reverse shell and network tunneling patterns
        re.compile(
            r"""(?:bash|sh|python|perl|ruby|php)\s+.*(?:/dev/tcp|/dev/udp|socket\(|connect\()""",
            re.IGNORECASE,
        ),
        # Block ALL shell variable expansion — agent has no need for env vars.
        # Use pwd/ls/which instead of $PWD/$HOME/$PATH.
        re.compile(r"""\$\{?[A-Z_][A-Z_0-9]*"""),
    ]

    @classmethod
    def check(cls, command: str) -> tuple[bool, str]:
        """Check if a bash command is allowed.

        Returns (True, "") if allowed, (False, reason) if denied.
        """
        if not command:
            return False, "Empty command"

        # Split on |, ;, &&, || and check each segment
        segments = re.split(r"\s*[|;]|\s*&&\s*|\s*\|\|\s*", command)
        for segment in segments:
            segment = segment.strip()
            if not segment:
                continue
            parts = segment.split()
            if not parts:
                continue
            base = parts[0]

            # Handle 'sudo' prefix
            if base == "sudo" and len(parts) > 1:
                base = parts[1]

            # Check deny list
            if base in cls._DENY_COMMANDS:
                return False, "This operation is not permitted."

            # Check deny patterns (anchored patterns use match, unanchored use search)
            for pattern in cls._DENY_PATTERNS:
                if (pattern.pattern.startswith("^") and pattern.match(segment)) or (
                    not pattern.pattern.startswith("^") and pattern.search(segment)
                ):
                    return False, "This operation is not permitted."

        return True, ""


class FileAccessFilter:
    """Pre-execute check for sensitive file reads.

    Checks file paths against sensitive patterns.
    Works for both absolute and relative paths.
    """

    _DENY_PATTERNS: Final[list[re.Pattern[str]]] = [
        re.compile(r"\.env(\.\w+)?$"),
        re.compile(r"\.claude/"),
        re.compile(r"CLAUDE\.md$"),
        re.compile(r"AGENTS\.md$"),
        re.compile(r"settings\.json$"),
        re.compile(r"Dockerfile", re.IGNORECASE),
        re.compile(r"docker-compose", re.IGNORECASE),
        re.compile(r"\.(conf|cfg|ini|yaml|yml)$"),
        re.compile(r"\.git/config$"),
        re.compile(r"pyproject\.toml$"),
        re.compile(r"package(-lock)?\.json$"),
        re.compile(r"uv\.lock$"),
        re.compile(r"\.(pem|key|crt)$"),
        re.compile(r"^/(proc|sys)/"),
    ]

    @classmethod
    def check(cls, path: str) -> tuple[bool, str]:
        """Check if a file path is allowed to be read.

        Returns (True, "") if allowed, (False, reason) if denied.
        """
        if not path:
            return False, "Empty path"

        for pattern in cls._DENY_PATTERNS:
            if pattern.search(path):
                return False, "This operation is not permitted."

        return True, ""

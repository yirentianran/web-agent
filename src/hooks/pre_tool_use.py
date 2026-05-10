#!/usr/bin/env python3
"""PreToolUse hook — block dangerous shell commands.

Reads JSON from stdin (tool input), outputs decision JSON to stdout.
Non-zero exit → CLI proceeds with default permission logic.
Exit 0 + JSON → CLI uses the specified decision.
"""

import json
import sys

DANGEROUS_PATTERNS = [
    "rm -rf /",
    "rm -rf ~",
    "curl ",
    "wget ",
    "nc ",
    "ncat ",
    "nmap ",
    "chmod 777",
    "mkfs",
    "dd if=",
    "mkfifo",
    "/dev/tcp/",
]

INFO_LEAK_COMMANDS = {
    "env",
    "printenv",
    "compgen",
    "set",
    "export",
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
}

INFO_LEAK_PATTERNS = [
    ("cat /proc/", "System info access"),
    ("docker ps", "Docker listing"),
    ("docker inspect", "Docker inspection"),
    ("docker info", "Docker info"),
    ("cat /etc/passwd", "System file access"),
    ("cat /etc/shadow", "System file access"),
    ("cat /etc/hosts", "System file access"),
    ("cat .env", "Config file access"),
]


def _deny(reason: str) -> None:
    """Print a deny decision and exit."""
    decision = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(decision))
    sys.exit(0)


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(1)

    cmd = input_data.get("tool_input", {}).get("command", "")
    file_path = input_data.get("tool_input", {}).get("file_path", "")

    # Check file_path for Read tool (independent of command)
    if file_path:
        sensitive_patterns = [".env", ".claude/", "CLAUDE.md", "AGENTS.md"]
        for pat in sensitive_patterns:
            if pat in file_path:
                _deny("This operation is not permitted.")

    if not cmd:
        sys.exit(1)

    for pattern in DANGEROUS_PATTERNS:
        if pattern in cmd:
            _deny(f"Dangerous command blocked: {cmd}")

    # Check info-leak commands
    cmd_stripped = cmd.strip()
    tokens = cmd_stripped.split()
    base_cmd = tokens[0] if tokens else ""

    # Handle 'sudo' prefix
    if base_cmd == "sudo" and len(tokens) > 1:
        base_cmd = tokens[1]

    if base_cmd in INFO_LEAK_COMMANDS:
        _deny("This operation is not permitted.")

    # Check info-leak patterns
    for pattern, _label in INFO_LEAK_PATTERNS:
        if pattern in cmd:
            _deny("This operation is not permitted.")

    # Allow — no output, CLI proceeds normally
    sys.exit(1)


if __name__ == "__main__":
    main()

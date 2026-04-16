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


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(1)

    cmd = input_data.get("tool_input", {}).get("command", "")
    if not cmd:
        sys.exit(1)

    for pattern in DANGEROUS_PATTERNS:
        if pattern in cmd:
            decision = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Dangerous command blocked: {cmd}",
                }
            }
            print(json.dumps(decision))
            sys.exit(0)

    # Allow — no output, CLI proceeds normally
    sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""PostToolUse hook — audit log for tool execution.

Appends each tool invocation to an append-only log file.
"""

import json
import sys
from datetime import datetime, timezone


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    tool_name = input_data.get("tool_name", "unknown")
    session_id = input_data.get("session_id", "unknown")
    timestamp = datetime.now(timezone.utc).isoformat()

    log_dir = "/workspace/.audit"
    try:
        import os
        os.makedirs(log_dir, exist_ok=True)
        with open(f"{log_dir}/tools.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "tool": tool_name,
                "session_id": session_id,
                "timestamp": timestamp,
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass  # Never fail the tool just because logging failed

    sys.exit(0)


if __name__ == "__main__":
    main()

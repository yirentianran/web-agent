#!/usr/bin/env python3
"""Stop hook — cleanup temporary files when a session ends."""

import shutil
import sys


def main() -> None:
    """Remove temp directories and stale lock files."""
    targets = [
        "/workspace/temp",
        "/workspace/.cache",
    ]
    for path in targets:
        try:
            shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass

    sys.exit(0)


if __name__ == "__main__":
    main()

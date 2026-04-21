"""Clean up redundant JSONL files after SQLite migration.

Removes:
- data/.msg-buffer/*.jsonl (duplicate of messages table)
- data/users/*/claude-data/sessions/*.jsonl (duplicate)
- data/users/*/claude-data/sessions/*.meta.json (stale)
- data/users/*/tasks/*.json (if tasks migrated to DB)
- data/users/*/memory.json (if memory migrated to DB)

Run after confirming all data is safely in SQLite.
Usage:
    uv run scripts/cleanup_stale_files.py --dry-run
    uv run scripts/cleanup_stale_files.py --confirm
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean up redundant files")
    parser.add_argument(
        "--confirm", action="store_true", help="Actually delete (default: dry-run)"
    )
    args = parser.parse_args()

    data_root = Path("data").resolve()
    if not data_root.exists():
        print(f"Data directory {data_root} does not exist. Nothing to clean.")
        return

    deleted = 0
    bytes_freed = 0
    dirs_removed = 0

    def remove(path: Path, label: str) -> None:
        nonlocal deleted, bytes_freed
        if args.confirm:
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                    dirs_removed += 1
                    print(f"  [DEL] dir: {path}")
                else:
                    size = path.stat().st_size
                    path.unlink()
                    bytes_freed += size
                    print(f"  [DEL] {path}")
            except OSError as e:
                print(f"  [ERR] {path}: {e}")
        else:
            try:
                size = path.stat().st_size if path.is_file() else 0
                bytes_freed += size
            except OSError:
                pass
            deleted += 1
            print(f"  [DRY] {path}")

    # 1. msg-buffer JSONL files
    msg_buffer = data_root / ".msg-buffer"
    if msg_buffer.exists():
        for f in sorted(msg_buffer.glob("*.jsonl")):
            remove(f, "msg-buffer")
        # Remove dir if empty after cleanup
        if args.confirm and msg_buffer.exists() and not any(msg_buffer.iterdir()):
            msg_buffer.rmdir()
            print(f"  [DEL] dir: {msg_buffer}")

    # 2. User-level claude-data sessions
    users_dir = data_root / "users"
    if users_dir.exists():
        for user_dir in sorted(users_dir.iterdir()):
            if not user_dir.is_dir():
                continue

            # claude-data/sessions/*.jsonl and *.meta.json
            sessions_dir = user_dir / "claude-data" / "sessions"
            if sessions_dir.exists():
                for f in sorted(sessions_dir.glob("*.jsonl")):
                    remove(f, "session-jsonl")
                for f in sorted(sessions_dir.glob("*.meta.json")):
                    remove(f, "session-meta")
                # Remove sessions dir if empty
                if args.confirm and sessions_dir.exists() and not any(sessions_dir.iterdir()):
                    sessions_dir.rmdir()
                    print(f"  [DEL] dir: {sessions_dir}")

            # tasks/*.json (only if DB is confirmed)
            tasks_dir = user_dir / "tasks"
            if tasks_dir.exists():
                for f in sorted(tasks_dir.glob("*.json")):
                    remove(f, "task-json")
                if args.confirm and tasks_dir.exists() and not any(tasks_dir.iterdir()):
                    tasks_dir.rmdir()
                    print(f"  [DEL] dir: {tasks_dir}")

            # memory.json
            mem_file = user_dir / "memory.json"
            if mem_file.exists():
                remove(mem_file, "memory-json")

    if args.confirm:
        print(f"\nDone. Deleted {deleted} files, {dirs_removed} dirs. "
              f"Freed {bytes_freed / 1024:.1f} KB.")
    else:
        print(f"\nDry run. Would delete {deleted} files. "
              f"Estimated {bytes_freed / 1024:.1f} KB freed.")
        print("Run with --confirm to actually delete.")


if __name__ == "__main__":
    main()

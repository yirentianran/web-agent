"""Session disk cleanup — evict old sessions to prevent unbounded disk growth."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Configurable thresholds
MAX_SESSION_AGE_DAYS = int(os.getenv("MAX_SESSION_AGE_DAYS", "30"))
MAX_TOTAL_DISK_MB = int(os.getenv("MAX_TOTAL_DISK_MB", "500"))

DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data"))


def cleanup_old_sessions(
    user_id: str,
    max_age_days: int = MAX_SESSION_AGE_DAYS,
    max_total_mb: int = MAX_TOTAL_DISK_MB,
) -> dict[str, int]:
    """Remove old session files based on age and total size.

    Returns {evicted_by_age: int, evicted_by_size: int, remaining: int}.
    """
    user_sessions_dir = DATA_ROOT / ".msg-buffer"
    if not user_sessions_dir.exists():
        return {"evicted_by_age": 0, "evicted_by_size": 0, "remaining": 0}

    evicted_by_age = 0
    evicted_by_size = 0
    cutoff = time.time() - (max_age_days * 86400)

    # Phase 1: Remove sessions older than max_age_days
    for session_file in user_sessions_dir.glob("*.jsonl"):
        try:
            mtime = session_file.stat().st_mtime
            if mtime < cutoff:
                session_file.unlink()
                evicted_by_age += 1
        except OSError:
            continue

    # Phase 2: If still over size limit, remove oldest sessions
    total_mb = _dir_size_mb(user_sessions_dir)
    if total_mb > max_total_mb:
        sessions_by_age = sorted(
            user_sessions_dir.glob("*.jsonl"),
            key=lambda f: f.stat().st_mtime,
        )
        for session_file in sessions_by_age:
            if _dir_size_mb(user_sessions_dir) <= max_total_mb:
                break
            try:
                session_file.unlink()
                evicted_by_size += 1
            except OSError:
                continue

    remaining = len(list(user_sessions_dir.glob("*.jsonl")))
    return {
        "evicted_by_age": evicted_by_age,
        "evicted_by_size": evicted_by_size,
        "remaining": remaining,
    }


def _dir_size_mb(path: Path) -> float:
    """Return total size of a directory in MB."""
    total = 0
    for f in path.iterdir():
        if f.is_file():
            total += f.stat().st_size
    return total / (1024 * 1024)

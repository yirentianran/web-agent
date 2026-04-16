"""Log cleanup — retention-based eviction for L2, L3, L4 logs.

Retention policy:
- L2 Application logs: 30 days (configurable via APP_LOG_RETENTION_DAYS)
- L3 Agent execution logs: 90 days (configurable via AGENT_LOG_RETENTION_DAYS)
- L4 Container logs: 7 days (configurable via CONTAINER_LOG_RETENTION_DAYS)

Usage:
    from src.log_cleanup import cleanup_old_logs

    result = cleanup_old_logs()
    # {"l2_evicted": 3, "l3_evicted": 0, "l4_evicted": 1}
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

APP_LOG_DIR = Path(os.getenv("APP_LOG_DIR", "/data/logs/app"))
APP_LOG_RETENTION_DAYS = int(os.getenv("APP_LOG_RETENTION_DAYS", "30"))

AGENT_LOG_DIR = Path(os.getenv("AGENT_LOG_DIR", "/data/logs/agent"))
AGENT_LOG_RETENTION_DAYS = int(os.getenv("AGENT_LOG_RETENTION_DAYS", "90"))

CONTAINER_LOG_DIR = Path(os.getenv("CONTAINER_LOG_DIR", "/data/logs/container"))
CONTAINER_LOG_RETENTION_DAYS = int(os.getenv("CONTAINER_LOG_RETENTION_DAYS", "7"))

AUDIT_LOG_DIR = Path(os.getenv("AUDIT_LOG_DIR", "/data/logs/audit"))
AUDIT_LOG_RETENTION_DAYS = int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "1095"))  # 3 years


def _evict_old_files(log_dir: Path, retention_days: int) -> int:
    """Remove files older than retention_days. Returns count of evicted files."""
    if not log_dir.exists():
        return 0

    cutoff = time.time() - (retention_days * 86400)
    evicted = 0

    for path in log_dir.rglob("*"):
        if path.is_file():
            try:
                mtime = path.stat().st_mtime
                if mtime < cutoff:
                    path.unlink()
                    evicted += 1
            except OSError:
                continue

    # Clean up empty directories
    for path in sorted(log_dir.rglob("*"), key=lambda p: len(str(p)), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            try:
                path.rmdir()
            except OSError:
                pass

    return evicted


def cleanup_old_logs() -> dict[str, int]:
    """Run retention-based log cleanup. Returns eviction counts."""
    return {
        "l2_app_evicted": _evict_old_files(APP_LOG_DIR, APP_LOG_RETENTION_DAYS),
        "l3_agent_evicted": _evict_old_files(AGENT_LOG_DIR, AGENT_LOG_RETENTION_DAYS),
        "l4_container_evicted": _evict_old_files(CONTAINER_LOG_DIR, CONTAINER_LOG_RETENTION_DAYS),
        "l1_audit_evicted": _evict_old_files(AUDIT_LOG_DIR, AUDIT_LOG_RETENTION_DAYS),
    }

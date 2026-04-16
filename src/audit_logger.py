"""L1 Audit Logger — immutable, append-only audit trail.

Writes one JSONL file per day per category (auth, skills, mcp, files, admin).
Each entry includes a hash chain for tamper detection.

Usage:
    from src.audit_logger import get_audit_logger

    audit = get_audit_logger()
    audit.log("auth", {"user_id": "alice", "action": "login", "result": "ok"})
    entries = audit.query("auth", user_id="alice")
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUDIT_LOG_DIR = Path(os.getenv("AUDIT_LOG_DIR", "/data/logs/audit"))

_CATEGORIES = {"auth", "skills", "mcp", "files", "admin", "session", "resource"}


def _hash_entry(entry: dict[str, Any], prev_hash: str) -> str:
    """Compute SHA-256 hash for tamper detection chain."""
    payload = json.dumps(entry, sort_keys=True) + prev_hash
    return hashlib.sha256(payload.encode()).hexdigest()


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class AuditLogger:
    """Append-only audit log with hash-chain tamper detection."""

    def __init__(self, base_dir: Path = AUDIT_LOG_DIR) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        # Cache: (category, date) -> last hash
        self._hash_cache: dict[tuple[str, str], str] = {}

    def log(self, category: str, data: dict[str, Any]) -> None:
        """Append an audit log entry. Raises ValueError for invalid category."""
        if category not in _CATEGORIES:
            raise ValueError(
                f"Invalid audit category: {category}. Must be one of {sorted(_CATEGORIES)}"
            )
        today = _today_str()
        log_file = self.base_dir / f"{category}-{today}.jsonl"

        prev_hash = self._hash_cache.get((category, today), self._load_last_hash(log_file))

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "epoch": time.time(),
            "category": category,
            **data,
        }
        entry["hash"] = _hash_entry(entry, prev_hash)

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        self._hash_cache[(category, today)] = entry["hash"]

    def query(
        self,
        category: str,
        *,
        date: str | None = None,
        user_id: str | None = None,
        action: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query audit log entries. Returns all matching entries."""
        if category not in _CATEGORIES:
            raise ValueError(
                f"Invalid audit category: {category}. Must be one of {sorted(_CATEGORIES)}"
            )

        results: list[dict[str, Any]] = []
        target_date = date or _today_str()
        log_file = self.base_dir / f"{category}-{target_date}.jsonl"

        if not log_file.exists():
            return results

        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if user_id and entry.get("user_id") != user_id:
                    continue
                if action and entry.get("action") != action:
                    continue
                results.append(entry)

        return results

    def verify_integrity(self, category: str, date: str | None = None) -> dict[str, Any]:
        """Verify the hash chain for a log file. Returns integrity report."""
        if category not in _CATEGORIES:
            raise ValueError(
                f"Invalid audit category: {category}. Must be one of {sorted(_CATEGORIES)}"
            )

        target_date = date or _today_str()
        log_file = self.base_dir / f"{category}-{target_date}.jsonl"

        if not log_file.exists():
            return {"valid": True, "entries": 0, "message": "no log file"}

        prev_hash = ""
        valid_count = 0
        invalid_count = 0
        total = 0

        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    invalid_count += 1
                    continue

                stored_hash = entry.pop("hash", "")
                expected = _hash_entry(entry, prev_hash)
                entry["hash"] = stored_hash  # restore

                if stored_hash == expected:
                    valid_count += 1
                else:
                    invalid_count += 1

                prev_hash = stored_hash

        return {
            "valid": invalid_count == 0,
            "entries": total,
            "valid_count": valid_count,
            "invalid_count": invalid_count,
        }

    def _load_last_hash(self, log_file: Path) -> str:
        """Load the last entry's hash from an existing log file."""
        if not log_file.exists():
            return ""
        last_line = ""
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last_line = line
        if not last_line:
            return ""
        try:
            return json.loads(last_line).get("hash", "")
        except json.JSONDecodeError:
            return ""


# Module-level singleton
_instance: AuditLogger | None = None


def get_audit_logger(base_dir: Path = AUDIT_LOG_DIR) -> AuditLogger:
    """Return the global AuditLogger singleton."""
    global _instance
    if _instance is None:
        _instance = AuditLogger(base_dir=base_dir)
    return _instance

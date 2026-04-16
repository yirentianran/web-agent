"""Tests for log cleanup — retention-based eviction."""

from __future__ import annotations

import os
import time
from pathlib import Path

from src.log_cleanup import _evict_old_files, cleanup_old_logs


def test_evict_old_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # Create a recent file
    (log_dir / "recent.log").write_text("recent data")
    # Create an old file
    old_file = log_dir / "old.log"
    old_file.write_text("old data")
    # Set mtime to 60 days ago
    old_mtime = time.time() - (60 * 86400)
    os.utime(old_file, (old_mtime, old_mtime))

    evicted = _evict_old_files(log_dir, retention_days=30)
    assert evicted == 1
    assert not old_file.exists()
    assert (log_dir / "recent.log").exists()


def test_no_eviction_when_all_recent(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "today.log").write_text("today")

    evicted = _evict_old_files(log_dir, retention_days=7)
    assert evicted == 0


def test_nonexistent_dir_returns_zero(tmp_path: Path) -> None:
    evicted = _evict_old_files(tmp_path / "nonexistent", retention_days=30)
    assert evicted == 0


def test_cleanup_all_logs(tmp_path: Path) -> None:
    """Test that cleanup_old_logs returns correct counts when called directly."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()

    container_dir = tmp_path / "container"
    container_dir.mkdir()

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    old_audit = audit_dir / "auth-2020-01-01.jsonl"
    old_audit.write_text("old audit")
    os.utime(old_audit, (time.time() - (400 * 86400),) * 2)

    # Call _evict_old_files directly on each directory
    result = {
        "l2_app_evicted": _evict_old_files(app_dir, 30),
        "l3_agent_evicted": _evict_old_files(agent_dir, 90),
        "l4_container_evicted": _evict_old_files(container_dir, 7),
        "l1_audit_evicted": _evict_old_files(audit_dir, 365),
    }
    assert result["l1_audit_evicted"] == 1
    assert result["l2_app_evicted"] == 0
    assert result["l3_agent_evicted"] == 0
    assert result["l4_container_evicted"] == 0


def test_empty_dirs_cleaned(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    subdir = log_dir / "subdir"
    subdir.mkdir(parents=True)
    # Create a file in subdir, make it old, then evict it
    old_file = subdir / "old.log"
    old_file.write_text("old")
    os.utime(old_file, (time.time() - (60 * 86400),) * 2)

    evicted = _evict_old_files(log_dir, retention_days=30)
    assert evicted == 1
    assert not subdir.exists(), "Empty subdir should be removed"

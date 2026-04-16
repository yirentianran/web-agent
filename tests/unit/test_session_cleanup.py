"""Unit tests for session disk cleanup."""

from __future__ import annotations

import os
import time
from pathlib import Path

from src.session_cleanup import cleanup_old_sessions


class TestSessionCleanup:
    def test_empty_directory(self, tmp_path: Path) -> None:
        """No sessions to clean."""
        buffer_dir = tmp_path / ".msg-buffer"
        buffer_dir.mkdir()

        import src.session_cleanup as sc
        original = sc.DATA_ROOT
        sc.DATA_ROOT = tmp_path

        result = cleanup_old_sessions("default")
        sc.DATA_ROOT = original

        assert result["evicted_by_age"] == 0
        assert result["evicted_by_size"] == 0
        assert result["remaining"] == 0

    def test_evicts_old_sessions(self, tmp_path: Path) -> None:
        """Sessions older than max_age_days are removed."""
        buffer_dir = tmp_path / ".msg-buffer"
        buffer_dir.mkdir()

        # Create an old session (31 days)
        old = buffer_dir / "session_old.jsonl"
        old.write_text("test\n")
        old_mtime = time.time() - (31 * 86400)
        os.utime(old, (old_mtime, old_mtime))

        # Create a recent session
        new = buffer_dir / "session_new.jsonl"
        new.write_text("test\n")

        import src.session_cleanup as sc
        original = sc.DATA_ROOT
        sc.DATA_ROOT = tmp_path

        result = cleanup_old_sessions("default", max_age_days=30)
        sc.DATA_ROOT = original

        assert result["evicted_by_age"] == 1
        assert result["remaining"] == 1
        assert not old.exists()
        assert new.exists()

    def test_does_not_evict_recent_sessions(self, tmp_path: Path) -> None:
        """Recent sessions are kept."""
        buffer_dir = tmp_path / ".msg-buffer"
        buffer_dir.mkdir()

        for i in range(3):
            f = buffer_dir / f"session_{i}.jsonl"
            f.write_text("test\n")

        import src.session_cleanup as sc
        original = sc.DATA_ROOT
        sc.DATA_ROOT = tmp_path

        result = cleanup_old_sessions("default", max_age_days=30, max_total_mb=500)
        sc.DATA_ROOT = original

        assert result["evicted_by_age"] == 0
        assert result["remaining"] == 3

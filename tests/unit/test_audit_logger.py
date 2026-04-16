"""Tests for L1 audit logger."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.audit_logger import AuditLogger, get_audit_logger


@pytest.fixture()
def tmp_audit_dir(tmp_path: Path) -> Path:
    return tmp_path / "audit"


class TestAuditLogger:
    def test_log_append(self, tmp_audit_dir: Path) -> None:
        logger = AuditLogger(base_dir=tmp_audit_dir)
        logger.log("auth", {"user_id": "alice", "action": "login", "result": "ok"})

        files = list(tmp_audit_dir.glob("auth-*.jsonl"))
        assert len(files) == 1

        entries = json.loads(files[0].read_text())
        assert entries["user_id"] == "alice"
        assert entries["action"] == "login"
        assert "hash" in entries

    def test_invalid_category_raises(self, tmp_audit_dir: Path) -> None:
        logger = AuditLogger(base_dir=tmp_audit_dir)
        with pytest.raises(ValueError, match="Invalid audit category"):
            logger.log("nonexistent", {"data": "test"})

    def test_query_by_user_id(self, tmp_audit_dir: Path) -> None:
        logger = AuditLogger(base_dir=tmp_audit_dir)
        logger.log("auth", {"user_id": "alice", "action": "login"})
        logger.log("auth", {"user_id": "bob", "action": "login"})
        logger.log("auth", {"user_id": "alice", "action": "token_create"})

        results = logger.query("auth", user_id="alice")
        assert len(results) == 2
        assert all(r["user_id"] == "alice" for r in results)

    def test_query_by_action(self, tmp_audit_dir: Path) -> None:
        logger = AuditLogger(base_dir=tmp_audit_dir)
        logger.log("auth", {"user_id": "alice", "action": "login"})
        logger.log("auth", {"user_id": "bob", "action": "logout"})

        results = logger.query("auth", action="login")
        assert len(results) == 1
        assert results[0]["user_id"] == "alice"

    def test_hash_chain_tamper_detection(self, tmp_audit_dir: Path) -> None:
        logger = AuditLogger(base_dir=tmp_audit_dir)
        logger.log("admin", {"user_id": "admin", "action": "delete_mcp"})
        logger.log("admin", {"user_id": "admin", "action": "create_mcp"})

        report = logger.verify_integrity("admin")
        assert report["valid"] is True
        assert report["entries"] == 2

    def test_tampered_file_detected(self, tmp_audit_dir: Path) -> None:
        logger = AuditLogger(base_dir=tmp_audit_dir)
        logger.log("auth", {"user_id": "alice", "action": "login"})

        # Tamper with the file
        log_file = list(tmp_audit_dir.glob("auth-*.jsonl"))[0]
        content = log_file.read_text()
        entry = json.loads(content.strip())
        entry["result"] = "hacked"
        log_file.write_text(json.dumps(entry) + "\n")

        report = logger.verify_integrity("auth")
        assert report["valid"] is False
        assert report["invalid_count"] == 1

    def test_query_empty_date_returns_empty(self, tmp_audit_dir: Path) -> None:
        logger = AuditLogger(base_dir=tmp_audit_dir)
        results = logger.query("auth", date="2000-01-01")
        assert results == []

    def test_multiple_days_separate_files(self, tmp_audit_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from datetime import datetime, timezone
        logger = AuditLogger(base_dir=tmp_audit_dir)
        logger.log("auth", {"user_id": "alice", "action": "login"})

        # Simulate next day
        class FakeDate:
            @staticmethod
            def now(tz=None):
                return datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc)

        monkeypatch.setattr("src.audit_logger.datetime", FakeDate)
        logger.log("auth", {"user_id": "alice", "action": "logout"})

        files = sorted(tmp_audit_dir.glob("auth-*.jsonl"))
        assert len(files) == 2

    def test_singleton_returns_same(self, tmp_audit_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.audit_logger
        src.audit_logger._instance = None
        logger1 = get_audit_logger(tmp_audit_dir)
        logger2 = get_audit_logger(tmp_audit_dir)
        assert logger1 is logger2
        src.audit_logger._instance = None

    def test_log_includes_category(self, tmp_audit_dir: Path) -> None:
        logger = AuditLogger(base_dir=tmp_audit_dir)
        logger.log("mcp", {"server": "test-server", "action": "register"})
        results = logger.query("mcp")
        assert len(results) == 1
        assert results[0]["category"] == "mcp"

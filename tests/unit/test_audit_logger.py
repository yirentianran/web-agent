"""Tests for L1 SQL-based audit logger."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.audit_logger import AuditLogger
from src.database import Database


@pytest.fixture()
async def audit_logger(tmp_path: Path) -> AuditLogger:
    db = Database(db_path=tmp_path / "test.db")
    await db.init()
    return AuditLogger(db=db)


class TestAuditLogger:
    async def test_log_and_query(self, audit_logger: AuditLogger) -> None:
        await audit_logger.log("auth", {"user_id": "alice", "action": "login", "result": "ok"})

        results = await audit_logger.query("auth")
        assert len(results) == 1
        assert results[0]["user_id"] == "alice"
        assert results[0]["action"] == "login"
        assert results[0]["category"] == "auth"

    async def test_invalid_category_raises(self, audit_logger: AuditLogger) -> None:
        with pytest.raises(ValueError, match="Invalid audit category"):
            await audit_logger.log("nonexistent", {"data": "test"})

    async def test_query_filter_by_user_id(self, audit_logger: AuditLogger) -> None:
        await audit_logger.log("auth", {"user_id": "alice", "action": "login"})
        await audit_logger.log("auth", {"user_id": "bob", "action": "login"})
        await audit_logger.log("auth", {"user_id": "alice", "action": "token_create"})

        results = await audit_logger.query("auth", user_id="alice")
        assert len(results) == 2
        assert all(r["user_id"] == "alice" for r in results)

    async def test_query_filter_by_action(self, audit_logger: AuditLogger) -> None:
        await audit_logger.log("auth", {"user_id": "alice", "action": "login"})
        await audit_logger.log("auth", {"user_id": "bob", "action": "logout"})

        results = await audit_logger.query("auth", action="login")
        assert len(results) == 1
        assert results[0]["user_id"] == "alice"

    async def test_query_empty_returns_empty(self, audit_logger: AuditLogger) -> None:
        results = await audit_logger.query("auth")
        assert results == []

    async def test_log_includes_category(self, audit_logger: AuditLogger) -> None:
        await audit_logger.log("mcp", {"server": "test-server", "action": "register"})
        results = await audit_logger.query("mcp")
        assert len(results) == 1
        assert results[0]["category"] == "mcp"

    async def test_multiple_entries_ordered_newest_first(self, audit_logger: AuditLogger) -> None:
        await audit_logger.log("auth", {"user_id": "alice", "action": "login"})
        await audit_logger.log("auth", {"user_id": "alice", "action": "logout"})

        results = await audit_logger.query("auth")
        assert len(results) == 2
        assert results[0]["action"] == "logout"  # newest first

    async def test_invalid_query_category_raises(self, audit_logger: AuditLogger) -> None:
        with pytest.raises(ValueError, match="Invalid audit category"):
            await audit_logger.query("nonexistent")

    async def test_sessions_category(self, audit_logger: AuditLogger) -> None:
        await audit_logger.log("session", {"user_id": "alice", "action": "create", "session_id": "s1"})
        results = await audit_logger.query("session")
        assert len(results) == 1
        assert results[0]["data"]["session_id"] == "s1"

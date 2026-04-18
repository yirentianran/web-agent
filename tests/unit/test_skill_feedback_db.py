"""Tests for skill feedback storage using SQLite."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from src.database import Database


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_feedback.db"


@pytest.fixture()
async def db(db_path: Path) -> Database:
    database = Database(db_path=db_path)
    await database.init()
    yield database
    await database.close()


# ── SkillFeedbackManager (DB-backed) ─────────────────────────────


class TestSkillFeedbackManagerDB:

    def _make_manager(self, db: Database) -> "DBSkillFeedbackManager":
        from src.skill_feedback import DBSkillFeedbackManager
        return DBSkillFeedbackManager(db=db)

    def test_submit_feedback_writes_to_db(self, db: Database) -> None:
        mgr = self._make_manager(db)
        entry = asyncio.get_event_loop().run_until_complete(
            mgr.submit_feedback("audit-pdf", user_id="alice", rating=4, comment="Good")
        )
        assert entry["rating"] == 4
        assert entry["skill_name"] == "audit-pdf"
        assert entry["user_id"] == "alice"

    def test_invalid_rating_raises(self, db: Database) -> None:
        mgr = self._make_manager(db)
        loop = asyncio.get_event_loop()
        with pytest.raises(ValueError, match="Rating must be between"):
            loop.run_until_complete(
                mgr.submit_feedback("test", user_id="alice", rating=0)
            )
        with pytest.raises(ValueError, match="Rating must be between"):
            loop.run_until_complete(
                mgr.submit_feedback("test", user_id="alice", rating=6)
            )

    def test_get_analytics_empty(self, db: Database) -> None:
        mgr = self._make_manager(db)
        loop = asyncio.get_event_loop()
        analytics = loop.run_until_complete(mgr.get_analytics("nonexistent"))
        assert analytics["total_feedbacks"] == 0
        assert analytics["average_rating"] == 0

    def test_get_analytics_with_feedback(self, db: Database) -> None:
        mgr = self._make_manager(db)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(mgr.submit_feedback("audit-pdf", user_id="alice", rating=5))
        loop.run_until_complete(mgr.submit_feedback("audit-pdf", user_id="bob", rating=3))
        loop.run_until_complete(mgr.submit_feedback("audit-pdf", user_id="carol", rating=4, comment="Decent"))

        analytics = loop.run_until_complete(mgr.get_analytics("audit-pdf"))
        assert analytics["total_feedbacks"] == 3
        assert analytics["average_rating"] == 4.0
        assert "5" in analytics["rating_distribution"]

    def test_get_user_feedback(self, db: Database) -> None:
        mgr = self._make_manager(db)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(mgr.submit_feedback("skill-a", user_id="alice", rating=5))
        loop.run_until_complete(mgr.submit_feedback("skill-b", user_id="alice", rating=3))
        loop.run_until_complete(mgr.submit_feedback("skill-a", user_id="bob", rating=4))

        items = loop.run_until_complete(mgr.get_user_feedback("alice"))
        assert len(items) == 2
        assert all(item["user_id"] == "alice" for item in items)

    def test_get_all_analytics(self, db: Database) -> None:
        mgr = self._make_manager(db)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(mgr.submit_feedback("skill-a", user_id="alice", rating=4))
        loop.run_until_complete(mgr.submit_feedback("skill-b", user_id="bob", rating=2))

        result = loop.run_until_complete(mgr.get_all_analytics())
        assert "skill-a" in result
        assert "skill-b" in result
        assert result["skill-a"]["average_rating"] == 4.0

    def test_comment_truncated(self, db: Database) -> None:
        mgr = self._make_manager(db)
        loop = asyncio.get_event_loop()
        long_comment = "x" * 1000
        entry = loop.run_until_complete(
            mgr.submit_feedback("test", user_id="alice", rating=3, comment=long_comment)
        )
        assert len(entry["comment"]) <= 500

    def test_user_edits_stored(self, db: Database) -> None:
        mgr = self._make_manager(db)
        loop = asyncio.get_event_loop()
        entry = loop.run_until_complete(
            mgr.submit_feedback(
                "test", user_id="alice", rating=4,
                comment="Good", user_edits="Fixed formatting"
            )
        )
        assert entry["user_edits"] == "Fixed formatting"

    def test_get_user_feedback_stats(self, db: Database) -> None:
        mgr = self._make_manager(db)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(mgr.submit_feedback("skill-a", user_id="alice", rating=5))
        loop.run_until_complete(mgr.submit_feedback("skill-a", user_id="alice", rating=4))
        loop.run_until_complete(mgr.submit_feedback("skill-b", user_id="alice", rating=3))

        result = loop.run_until_complete(mgr.get_user_feedback_stats("alice"))
        assert len(result["stats"]) == 2
        assert result["total_count"] == 3
        # Find skill-a stats
        skill_a = next(s for s in result["stats"] if s["skill_name"] == "skill-a")
        assert skill_a["count"] == 2
        assert skill_a["avg_rating"] == 4.5

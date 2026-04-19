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


# ── DB-backed evolution methods ─────────────────────────────────


class TestDBEvolutionMethods:

    def _make_manager(self, db: Database) -> "DBSkillFeedbackManager":
        from src.skill_feedback import DBSkillFeedbackManager
        return DBSkillFeedbackManager(db=db)

    def test_get_evolution_candidates(self, db: Database) -> None:
        """Skills with >= 10 feedback and avg < 4.5 should be candidates."""
        mgr = self._make_manager(db)
        loop = asyncio.get_event_loop()
        # Bad skill: 12 entries, avg 2.0
        for i in range(12):
            loop.run_until_complete(
                mgr.submit_feedback("bad-skill", user_id=f"user{i}", rating=2)
            )
        # Good skill: 12 entries, avg 5.0
        for i in range(12):
            loop.run_until_complete(
                mgr.submit_feedback("good-skill", user_id=f"user{i}", rating=5)
            )

        candidates = loop.run_until_complete(mgr.get_evolution_candidates())
        names = [c["skill_name"] for c in candidates]
        assert "bad-skill" in names
        assert "good-skill" not in names

    def test_get_feedback_for_evolution(self, db: Database) -> None:
        """Should return high-quality, low-rated, and user_edits entries."""
        mgr = self._make_manager(db)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            mgr.submit_feedback("test", user_id="alice", rating=5, comment="Great!")
        )
        loop.run_until_complete(
            mgr.submit_feedback("test", user_id="bob", rating=1, comment="Broken", user_edits="Fixed the bug")
        )

        feedback = loop.run_until_complete(mgr.get_feedback_for_evolution("test"))
        assert len(feedback["high_quality"]) == 1
        assert len(feedback["low_rated"]) == 1
        assert feedback["low_rated"][0]["comment"] == "Broken"
        assert len(feedback["user_edits"]) == 1
        assert feedback["user_edits"][0]["user_edits"] == "Fixed the bug"

    def test_get_all_feedback_returns_entries_from_all_users(self, db: Database) -> None:
        """Should return feedback entries from all users, not just one."""
        mgr = self._make_manager(db)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(mgr.submit_feedback("skill-a", user_id="alice", rating=5))
        loop.run_until_complete(mgr.submit_feedback("skill-b", user_id="bob", rating=3))
        loop.run_until_complete(mgr.submit_feedback("skill-a", user_id="carol", rating=4, comment="Nice"))

        items = loop.run_until_complete(mgr.get_all_feedback())
        assert len(items) == 3
        user_ids = {item["user_id"] for item in items}
        assert "alice" in user_ids
        assert "bob" in user_ids
        assert "carol" in user_ids

    def test_rollback_fails_when_no_backup(self, db: Database, tmp_path: Path) -> None:
        """Rollback should return None when there are no backup files."""
        mgr = self._make_manager(db)
        loop = asyncio.get_event_loop()
        # Create skill dir with SKILL.md but no backups
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Original")

        result = loop.run_until_complete(mgr.rollback_version("test-skill", tmp_path))
        assert result is None

    def test_activate_creates_backup(self, db: Database, tmp_path: Path) -> None:
        """Activating a version should back up the current SKILL.md."""
        mgr = self._make_manager(db)
        loop = asyncio.get_event_loop()
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Original")
        (skill_dir / "SKILL_v1.md").write_text("# Improved")

        result = loop.run_until_complete(mgr.activate_version("test-skill", version_number=1, skills_dir=tmp_path))
        assert result is not None
        assert result["activated"] is True
        # Verify backup exists
        backups = list(skill_dir.glob("SKILL_backup_v*.md"))
        assert len(backups) == 1
        assert backups[0].read_text() == "# Original"
        # Verify SKILL.md has new content
        assert (skill_dir / "SKILL.md").read_text() == "# Improved"

    def test_rollback_restores_backup(self, db: Database, tmp_path: Path) -> None:
        """Rollback should restore the most recent backup."""
        mgr = self._make_manager(db)
        loop = asyncio.get_event_loop()
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        # Set up: backup exists
        (skill_dir / "SKILL.md").write_text("# New version")
        (skill_dir / "SKILL_backup_v1.md").write_text("# Original backup")

        result = loop.run_until_complete(mgr.rollback_version("test-skill", tmp_path))
        assert result is not None
        assert result["rolled_back"] is True
        assert (skill_dir / "SKILL.md").read_text() == "# Original backup"
        # Current should be backed up
        current_backups = list(skill_dir.glob("SKILL_backup_current_*.md"))
        assert len(current_backups) == 1
        assert current_backups[0].read_text() == "# New version"

"""Tests for skill evolution: feedback collection, should_evolve, version generation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from src.skill_evolution import (
    SkillEvolutionManager,
)

if TYPE_CHECKING:
    from src.database import Database


class TestDBBackedEvolution:
    """Test async DB-backed methods in SkillEvolutionManager."""

    def _init_db(self, tmp_path: Path) -> Database:
        from src.database import Database
        db = Database(db_path=tmp_path / "test_evolution.db")
        asyncio.get_event_loop().run_until_complete(db.init())
        return db

    def _create_user(self, db: Database, user_id: str) -> None:
        conn = db.connection()
        loop = asyncio.get_event_loop()

        async def _insert():
            async with conn as c:
                await c.execute(
                    "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
                    (user_id,),
                )

        loop.run_until_complete(_insert())

    def test_db_get_feedback_stats_empty(self, tmp_path: Path) -> None:
        db = self._init_db(tmp_path)
        try:
            mgr = SkillEvolutionManager(db=db)
            loop = asyncio.get_event_loop()
            stats = loop.run_until_complete(mgr.db_get_feedback_stats("nonexistent"))
            assert stats.count == 0
        finally:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(db.close())

    def test_db_get_feedback_stats_with_data(self, tmp_path: Path) -> None:
        db = self._init_db(tmp_path)
        try:
            self._create_user(db, "alice")
            self._create_user(db, "bob")
            from src.skill_feedback import DBSkillFeedbackManager
            db_mgr = DBSkillFeedbackManager(db=db)
            loop = asyncio.get_event_loop()
            loop.run_until_complete(db_mgr.submit_feedback("test", user_id="alice", rating=5))
            loop.run_until_complete(db_mgr.submit_feedback("test", user_id="bob", rating=3))

            mgr = SkillEvolutionManager(db=db)
            stats = loop.run_until_complete(mgr.db_get_feedback_stats("test"))
            assert stats.count == 2
            assert stats.average_rating == 4.0
        finally:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(db.close())

    def test_db_should_evolve_false_when_no_data(self, tmp_path: Path) -> None:
        db = self._init_db(tmp_path)
        try:
            mgr = SkillEvolutionManager(db=db)
            loop = asyncio.get_event_loop()
            result = loop.run_until_complete(mgr.db_should_evolve("nonexistent"))
            assert not result
        finally:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(db.close())

    def test_db_get_feedback_for_evolution(self, tmp_path: Path) -> None:
        """SkillEvolutionManager should delegate get_feedback_for_evolution to DBSkillFeedbackManager."""
        db = self._init_db(tmp_path)
        try:
            self._create_user(db, "alice")
            self._create_user(db, "bob")
            from src.skill_feedback import DBSkillFeedbackManager
            db_mgr = DBSkillFeedbackManager(db=db)
            loop = asyncio.get_event_loop()
            loop.run_until_complete(
                db_mgr.submit_feedback("test", user_id="alice", rating=5, comment="Great!")
            )
            loop.run_until_complete(
                db_mgr.submit_feedback("test", user_id="bob", rating=1, comment="Broken", user_edits="Fixed the bug")
            )

            mgr = SkillEvolutionManager(db=db)
            feedback = loop.run_until_complete(mgr.db_get_feedback_for_evolution("test"))
            assert len(feedback["high_quality"]) == 1
            assert len(feedback["low_rated"]) == 1
            assert feedback["low_rated"][0]["comment"] == "Broken"
            assert len(feedback["user_edits"]) == 1
            assert feedback["user_edits"][0]["user_edits"] == "Fixed the bug"
        finally:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(db.close())

    def test_db_get_evolution_candidates(self, tmp_path: Path) -> None:
        db = self._init_db(tmp_path)
        try:
            for i in range(15):
                self._create_user(db, f"user{i}")
            from src.skill_feedback import DBSkillFeedbackManager
            db_mgr = DBSkillFeedbackManager(db=db)
            loop = asyncio.get_event_loop()
            # Bad skill: 12 entries, avg 2.0
            for i in range(12):
                loop.run_until_complete(
                    db_mgr.submit_feedback("bad-skill", user_id=f"user{i}", rating=2)
                )
            # Good skill: 15 entries, avg 5.0
            for i in range(15):
                loop.run_until_complete(
                    db_mgr.submit_feedback("good-skill", user_id=f"user{i}", rating=5)
                )

            mgr = SkillEvolutionManager(db=db)
            candidates = loop.run_until_complete(mgr.db_get_evolution_candidates())
            names = [c.skill_name for c in candidates]
            assert "bad-skill" in names
            assert "good-skill" not in names
        finally:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(db.close())

"""Tests for collective intelligence modules."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest

from src.database import Database


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
async def db(tmp_path: Path) -> Database:
    d = Database(db_path=tmp_path / "test.db")
    await d.init()
    yield d
    await d.close()


# ── Task 1: Database migration ────────────────────────────────────────


class TestCollectiveIntelligenceMigration:
    @pytest.mark.asyncio
    async def test_migration_creates_wiki_pages_table(self, db: Database) -> None:
        """migrate_collective_intelligence should create wiki_pages table."""
        await db.migrate_collective_intelligence()
        async with db.connection() as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='wiki_pages'"
            )
            row = await cursor.fetchone()
        assert row is not None, "wiki_pages table should exist"

    @pytest.mark.asyncio
    async def test_migration_is_idempotent(self, db: Database) -> None:
        """Running migrate_collective_intelligence twice should not fail."""
        await db.migrate_collective_intelligence()
        await db.migrate_collective_intelligence()
        # No exception = success

    @pytest.mark.asyncio
    async def test_migration_creates_all_tables(self, db: Database) -> None:
        """All collective intelligence tables should be created."""
        await db.migrate_collective_intelligence()
        async with db.connection() as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                "('wiki_pages', 'session_summaries', "
                "'skill_promotion_queue', 'learned_patterns')"
            )
            rows = await cursor.fetchall()
        names = {r[0] for r in rows}
        expected = {
            "wiki_pages", "session_summaries",
            "skill_promotion_queue", "learned_patterns",
        }
        assert names == expected, f"Missing tables: {expected - names}"

    @pytest.mark.asyncio
    async def test_migration_creates_fts5_indexes(self, db: Database) -> None:
        """FTS5 virtual tables should be created."""
        await db.migrate_collective_intelligence()
        async with db.connection() as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                "('wiki_fts', 'session_summary_fts')"
            )
            rows = await cursor.fetchall()
        names = {r[0] for r in rows}
        assert names == {"wiki_fts", "session_summary_fts"}


# ── Task 3: Wiki generator ────────────────────────────────────────────


class TestWikiGenerator:
    def test_generate_id(self) -> None:
        """_generate_id should create a slug-based page ID."""
        from src.wiki_generator import WikiGenerator

        gen = WikiGenerator.__new__(WikiGenerator)
        page_id = gen._generate_id("SQLite database locked error")
        assert "sqlite" in page_id.lower() or "database" in page_id.lower()

    def test_generate_id_strips_special_chars(self) -> None:
        """Special characters should be stripped from page ID."""
        from src.wiki_generator import WikiGenerator

        gen = WikiGenerator.__new__(WikiGenerator)
        page_id = gen._generate_id("Error: 500 (Internal Server!)")
        assert "error-500-internal-server" in page_id.lower()


# ── Task 4: Semantic search ───────────────────────────────────────────


class TestSemanticSearch:
    def test_anonymize_summary_strips_paths(self) -> None:
        """anonymize_summary should strip file paths."""
        from src.semantic_search import anonymize_summary

        result = anonymize_summary(
            "User alice fixed /home/alice/project/main.py error"
        )
        assert "/home/" not in result
        assert "<path>" in result

    def test_anonymize_summary_strips_user_ids(self) -> None:
        """anonymize_summary should strip user IDs."""
        from src.semantic_search import anonymize_summary

        result = anonymize_summary("user_id=alice fixed the issue")
        assert "alice" not in result
        assert "user: <anonymized>" in result

    def test_anonymize_summary_strips_session_ids(self) -> None:
        """anonymize_summary should strip session IDs."""
        from src.semantic_search import anonymize_summary

        result = anonymize_summary("In session sess_abc123def456 the user...")
        assert "sess_abc123def456" not in result
        assert "<session>" in result


# ── Task 5: Auto-promotion ────────────────────────────────────────────


class TestAutoPromotion:
    @pytest.mark.asyncio
    async def test_check_auto_promotion_returns_empty_when_no_skills(self) -> None:
        """check_auto_promotion should return empty list when no personal skills exist."""
        from src.skill_manager import SkillManager

        mock_db = MagicMock()

        class FakeConn:
            async def __aenter__(self):
                return mock_db

            async def __aexit__(self, *args):
                pass

        mock_cursor = MagicMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_db.execute = AsyncMock(return_value=mock_cursor)
        mock_db.connection = MagicMock(return_value=FakeConn())

        mgr = SkillManager(db=mock_db)
        result = await mgr.check_auto_promotion()
        assert isinstance(result, list)
        assert len(result) == 0


# ── Task 6: Pattern learner ───────────────────────────────────────────


class TestPatternLearner:
    @pytest.mark.asyncio
    async def test_extract_tool_patterns_returns_empty_on_empty_db(self) -> None:
        """extract_tool_patterns should return empty dict when no tool data exists."""
        from src.pattern_learner import PatternLearner

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_conn.execute = AsyncMock(return_value=mock_cursor)
        mock_conn.commit = AsyncMock(return_value=None)

        class FakeConn:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *args):
                pass

        mock_db = MagicMock()
        mock_db.connection = MagicMock(return_value=FakeConn())

        learner = PatternLearner(db=mock_db)
        result = await learner.extract_tool_patterns()
        assert "tool_pairs" in result
        assert "tool_success_rates" in result

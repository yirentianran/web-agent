# tests/unit/test_instinct_extractor.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.instinct_extractor import InstinctStore, InstinctExtractor


class TestInstinctStore:
    @pytest.mark.asyncio
    async def test_upsert_new(self):
        db = MagicMock()
        conn = AsyncMock()
        db.connection.return_value.__aenter__.return_value = conn
        conn.execute.return_value = AsyncMock()
        conn.execute.return_value.fetchone.return_value = None
        conn.execute.return_value.lastrowid = 1

        store = InstinctStore(db)
        new_id = await store.upsert(
            domain="tool_usage", normalized_trigger="grep-first",
            trigger="when editing files", action="run Grep before Edit",
        )
        assert new_id == 1

    @pytest.mark.asyncio
    async def test_upsert_existing_merges(self):
        db = MagicMock()
        conn = AsyncMock()
        db.connection.return_value.__aenter__.return_value = conn
        conn.execute.return_value = AsyncMock()
        conn.execute.return_value.fetchone.return_value = (5, 0.5, 3, 2)
        conn.execute.return_value.lastrowid = 99

        store = InstinctStore(db)
        new_id = await store.upsert(
            domain="tool_usage", normalized_trigger="grep-first",
            trigger="when editing", action="run Grep before Edit",
        )
        assert new_id == 5  # returns existing ID

    @pytest.mark.asyncio
    async def test_adjust_confidence(self):
        db = MagicMock()
        conn = AsyncMock()
        db.connection.return_value.__aenter__.return_value = conn

        store = InstinctStore(db)
        await store.adjust_confidence(1, -0.1)
        conn.execute.assert_called_once()


class TestInstinctExtractor:
    def test_filter_consecutive_failures(self):
        extractor = InstinctExtractor(
            db=MagicMock(), obs_store=MagicMock(),
            instinct_store=MagicMock(), evolution_store=MagicMock(),
            skill_manager=MagicMock(), data_root="/tmp",
        )
        events = [
            {"id": 1, "session_id": "s1", "event_type": "tool_call_end",
             "tool_name": "Read", "success": False},
            {"id": 2, "session_id": "s1", "event_type": "tool_call_end",
             "tool_name": "Read", "success": False},
            {"id": 3, "session_id": "s1", "event_type": "tool_call_end",
             "tool_name": "Write", "success": True},
        ]
        result = extractor._filter_significant_events(events)
        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["id"] == 2

    def test_filter_skips_isolated_failures(self):
        extractor = InstinctExtractor(
            db=MagicMock(), obs_store=MagicMock(),
            instinct_store=MagicMock(), evolution_store=MagicMock(),
            skill_manager=MagicMock(), data_root="/tmp",
        )
        events = [
            {"id": 1, "session_id": "s1", "event_type": "tool_call_end",
             "tool_name": "Read", "success": False},
            {"id": 2, "session_id": "s1", "event_type": "tool_call_end",
             "tool_name": "Write", "success": True},
        ]
        result = extractor._filter_significant_events(events)
        assert len(result) == 0

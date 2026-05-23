# tests/unit/test_observation.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.observation import ObservationStore


@pytest.mark.asyncio
async def test_record_observation():
    db = MagicMock()
    conn = AsyncMock()
    db.connection.return_value.__aenter__.return_value = conn
    cursor = AsyncMock()
    cursor.lastrowid = 42
    conn.execute.return_value = cursor

    store = ObservationStore(db)
    obs_id = await store.record(
        session_id="s1", user_id="u1", event_type="tool_call_end",
        tool_name="Read", success=True, duration_ms=150,
    )
    assert obs_id == 42
    conn.execute.assert_called_once()
    sql = conn.execute.call_args[0][0]
    assert "INSERT INTO observations" in sql


@pytest.mark.asyncio
async def test_count_since():
    db = MagicMock()
    conn = AsyncMock()
    db.connection.return_value.__aenter__.return_value = conn
    conn.execute.return_value = AsyncMock()
    conn.execute.return_value.fetchone.return_value = (15,)

    store = ObservationStore(db)
    count = await store.count_since(1000.0)
    assert count == 15

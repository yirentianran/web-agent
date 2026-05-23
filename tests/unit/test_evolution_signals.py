# tests/unit/test_evolution_signals.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.evolution_signals import EvolutionSignals


@pytest.mark.asyncio
async def test_compute_daily_snapshot():
    db = MagicMock()
    conn = AsyncMock()
    db.connection.return_value.__aenter__.return_value = conn
    conn.execute_fetchall.return_value = [(1, 80), (0, 20)]

    signals = EvolutionSignals(
        db=db, evolution_store=MagicMock(), skill_manager=MagicMock()
    )
    snapshot = await signals._compute_daily_snapshot(
        {"id": 1, "skill_name": "test-skill"}, "2026-05-22"
    )
    assert snapshot is not None
    assert snapshot["evolution_log_id"] == 1
    assert snapshot["snapshot_date"] == "2026-05-22"
    assert 0 <= snapshot["composite_score"] <= 1


@pytest.mark.asyncio
async def test_run_daily_eval_no_degradation():
    db = MagicMock()
    evo_store = MagicMock()
    evo_store.get_active_evolutions = AsyncMock(return_value=[
        {"id": 1, "skill_name": "test", "baseline_composite": 0.5}
    ])
    evo_store.get_last_snapshots = AsyncMock(return_value=[
        {"composite_score": 0.8}, {"composite_score": 0.75},
        {"composite_score": 0.7}, {"composite_score": 0.8},
        {"composite_score": 0.85}, {"composite_score": 0.9},
        {"composite_score": 0.95},
    ])
    evo_store.create_snapshot = AsyncMock(return_value=1)
    evo_store.get_expired_reviews = AsyncMock(return_value=[])
    evo_store.update_status = AsyncMock()

    signals = EvolutionSignals(
        db=db, evolution_store=evo_store, skill_manager=MagicMock()
    )
    signals._compute_daily_snapshot = AsyncMock(return_value={
        "evolution_log_id": 1, "snapshot_date": "2026-05-22",
        "usage_count": 50, "unique_users": 5, "avg_rating": 0,
        "session_success_rate": 0.9, "composite_score": 0.85,
    })

    result = await signals.run_daily_eval()
    assert result["evaluated"] >= 1
    assert result["degraded"] == 0

"""Tests for evolution log CRUD and evaluator."""
from __future__ import annotations

import time

import pytest


@pytest.mark.asyncio
async def test_create_log_returns_id(db):
    from src.evolution_log import EvolutionLogStore
    store = EvolutionLogStore(db)
    result = await store.create_log("test-skill", "1.0", "1.1", evolve_reason="test")
    assert result["id"] is not None
    assert isinstance(result["id"], int)


@pytest.mark.asyncio
async def test_list_logs_filters_by_status(db):
    from src.evolution_log import EvolutionLogStore
    store = EvolutionLogStore(db)
    await store.create_log("skill-a", "1.0", "1.1")
    await store.create_log("skill-b", "2.0", "2.1")
    result = await store.list_logs(status="active")
    assert result["total"] == 2


@pytest.mark.asyncio
async def test_list_logs_empty(db):
    from src.evolution_log import EvolutionLogStore
    store = EvolutionLogStore(db)
    result = await store.list_logs(status="rolled_back")
    assert result["total"] == 0
    assert result["items"] == []


@pytest.mark.asyncio
async def test_create_and_get_snapshots(db):
    from src.evolution_log import EvolutionLogStore
    store = EvolutionLogStore(db)
    r = await store.create_log("s", "1.0", "1.1")
    log_id = r["id"]
    await store.create_snapshot(log_id, "2026-05-22", 10, 3, 4.0, 0.85, 0.72)
    await store.create_snapshot(log_id, "2026-05-23", 8, 2, 3.5, 0.80, 0.60)
    snaps = await store.get_snapshots(log_id)
    assert len(snaps) == 2
    assert snaps[0]["composite_score"] == 0.72


@pytest.mark.asyncio
async def test_get_last_snapshots_returns_correct_count(db):
    from src.evolution_log import EvolutionLogStore
    store = EvolutionLogStore(db)
    r = await store.create_log("s", "1.0", "1.1")
    for i in range(10):
        await store.create_snapshot(r["id"], f"2026-05-{22+i:02d}", 5, 2, 3.0, 0.7, 0.5)
    snaps = await store.get_last_snapshots(r["id"], count=7)
    assert len(snaps) == 7


@pytest.mark.asyncio
async def test_get_active_evolutions_filters_rolled_back(db):
    from src.evolution_log import EvolutionLogStore
    store = EvolutionLogStore(db)
    r = await store.create_log("s", "1.0", "1.1")
    await store.update_status(r["id"], "rolled_back")
    active = await store.get_active_evolutions()
    assert len(active) == 0


@pytest.mark.asyncio
async def test_get_expired_reviews(db):
    from src.evolution_log import EvolutionLogStore
    store = EvolutionLogStore(db)
    r = await store.create_log("s", "1.0", "1.1")
    await store.update_status(r["id"], "under_review", auto_rollback_at=int(time.time()) - 3600)
    expired = await store.get_expired_reviews()
    assert len(expired) == 1
    assert expired[0]["id"] == r["id"]

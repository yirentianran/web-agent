"""Tests for evolution admin APIs."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_mock_sdk = MagicMock()
_mock_sdk.types = MagicMock()
_mock_sdk.types.UserMessage = MagicMock
sys.modules["claude_agent_sdk"] = _mock_sdk
sys.modules["claude_agent_sdk.types"] = _mock_sdk.types

from fastapi.testclient import TestClient

import main_server
import src.auth
import src.admin_auth

src.auth.ENFORCE_AUTH = False
src.admin_auth.ENFORCE_AUTH = False


@pytest.fixture(autouse=True)
async def _patch_data_root(tmp_path: Path):
    """Redirect DATA_ROOT to a temporary directory and initialize a test DB."""
    main_server.DATA_ROOT = tmp_path
    main_server.buffer = main_server.MessageBuffer()
    main_server.active_tasks.clear()
    main_server.pending_answers.clear()
    (tmp_path / "users").mkdir(exist_ok=True)

    # Initialize a test SQLite database
    from src.database import Database

    db_path = tmp_path / "test.db"
    main_server._db = Database(db_path=db_path)
    await main_server._db.init()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(main_server.app)


class TestEvolutionOverview:
    def test_returns_empty_list_with_no_evolutions(self, client):
        resp = client.get("/api/admin/evolution/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []


class TestEvolutionDetail:
    def test_404_for_missing_evolution(self, client):
        resp = client.get("/api/admin/evolution/99999")
        assert resp.status_code == 404


class TestEvolutionReview:
    def test_422_for_invalid_decision(self, client):
        resp = client.post("/api/admin/evolution/1/review", json={"decision": "maybe"})
        assert resp.status_code == 422


class TestTrendAggregation:
    """Unit tests for trend data computation logic."""

    def test_aggregates_rows_into_trend_points(self):
        rows = [
            ("2026-06-17", 10, 8),
            ("2026-06-18", 5, 5),
        ]
        trend = []
        for r in rows:
            total = r[1]
            success = r[2] or 0
            trend.append({
                "date": r[0],
                "success_rate": round(success / total, 4) if total > 0 else 1.0,
                "usage_count": total,
            })
        assert trend[0] == {"date": "2026-06-17", "success_rate": 0.8, "usage_count": 10}
        assert trend[1] == {"date": "2026-06-18", "success_rate": 1.0, "usage_count": 5}

    def test_zero_total_returns_success_rate_1(self):
        rows = [("2026-06-17", 0, 0)]
        trend = []
        for r in rows:
            total = r[1]
            success = r[2] or 0
            trend.append({
                "date": r[0],
                "success_rate": round(success / total, 4) if total > 0 else 1.0,
                "usage_count": total,
            })
        assert trend[0]["success_rate"] == 1.0


class TestSignalsDeltaPct:
    """Unit tests for delta percentage computation."""

    def _delta_pct(self, cur, base):
        if base == 0:
            return 100.0 if cur > 0 else 0.0
        return round((cur - base) / base * 100, 1)

    def test_positive_delta(self):
        assert self._delta_pct(0.85, 0.80) == 6.2

    def test_no_change(self):
        assert self._delta_pct(0.5, 0.5) == 0.0

    def test_zero_both(self):
        assert self._delta_pct(0, 0) == 0.0

    def test_from_zero_baseline_with_usage(self):
        assert self._delta_pct(5, 0) == 100.0

    def test_negative_delta(self):
        assert self._delta_pct(0.7, 1.0) == -30.0


class TestL4InstinctContext:
    """Tests for _load_instinct_context helper."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_keyword_match(self):
        mock_db = MagicMock()
        mock_conn = MagicMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)
        mock_conn.execute_fetchall = AsyncMock(return_value=[
            ("trigger_word", "Do X when Y"),
        ])
        mock_db.connection.return_value = mock_conn

        from main_server import _load_instinct_context
        result = await _load_instinct_context("completely unrelated words", mock_db)
        assert result == ""

    @pytest.mark.asyncio
    async def test_matches_keywords_and_formats_context(self):
        mock_db = MagicMock()
        mock_conn = MagicMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)
        mock_conn.execute_fetchall = AsyncMock(return_value=[
            ("use python data", "Prefer Python for data processing"),
        ])
        mock_db.connection.return_value = mock_conn

        from main_server import _load_instinct_context
        result = await _load_instinct_context("process data with python", mock_db)
        assert "## Learned Patterns" in result
        assert "Prefer Python for data processing" in result

    @pytest.mark.asyncio
    async def test_limits_to_top_3_instincts(self):
        mock_db = MagicMock()
        mock_conn = MagicMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)
        mock_conn.execute_fetchall = AsyncMock(return_value=[
            ("python", "Guidance A"),
            ("python", "Guidance B"),
            ("python", "Guidance C"),
            ("python", "Guidance D"),
            ("python", "Guidance E"),
        ])
        mock_db.connection.return_value = mock_conn

        from main_server import _load_instinct_context
        result = await _load_instinct_context("python", mock_db)
        # Should only contain 3 guidance items
        assert result.count("- ") == 3

    @pytest.mark.asyncio
    async def test_handles_db_exception_gracefully(self):
        mock_db = MagicMock()
        mock_db.connection.side_effect = Exception("DB down")

        from main_server import _load_instinct_context
        result = await _load_instinct_context("test query", mock_db)
        assert result == ""

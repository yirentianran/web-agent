"""Tests for dashboard aggregation APIs."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Prevent SDK from being imported (it's not installed in test environments)
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
def _patch_data_root(tmp_path: Path) -> None:
    """Redirect DATA_ROOT to a temporary directory for each test."""
    main_server.DATA_ROOT = tmp_path
    main_server.buffer = main_server.MessageBuffer()
    main_server.active_tasks.clear()
    main_server.pending_answers.clear()
    (tmp_path / "users").mkdir(exist_ok=True)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(main_server.app)


class TestDashboardOverview:
    def test_overview_returns_expected_structure(self, client):
        """Overview endpoint returns correct JSON keys even with empty DB."""
        resp = client.get(
            "/api/admin/dashboard/overview?from_date=2026-01-01&to_date=2026-01-31"
        )
        assert resp.status_code == 200
        data = resp.json()
        for key in ("active_users", "total_users", "new_users", "total_sessions",
                     "total_input_tokens", "total_output_tokens",
                     "total_cache_read_tokens", "total_cache_write_tokens"):
            assert key in data, f"Missing key: {key}"
        assert data["active_users"] == 0

    def test_overview_validates_date_range(self, client):
        """from > to should return 422."""
        resp = client.get(
            "/api/admin/dashboard/overview?from_date=2026-12-31&to_date=2026-01-01"
        )
        assert resp.status_code == 422

    def test_overview_rejects_range_over_365_days(self, client):
        """Range > 365 days should return 422."""
        resp = client.get(
            "/api/admin/dashboard/overview?from_date=2025-01-01&to_date=2026-12-31"
        )
        assert resp.status_code == 422

    def test_overview_rejects_invalid_date_format(self, client):
        """Invalid date strings should return 422."""
        resp = client.get(
            "/api/admin/dashboard/overview?from_date=abc&to_date=2026-01-31"
        )
        assert resp.status_code == 422

    def test_overview_defaults_to_30_days(self, client):
        """No params should default to last 30 days."""
        resp = client.get("/api/admin/dashboard/overview")
        assert resp.status_code == 200


class TestDashboardTrends:
    def test_trends_returns_expected_structure(self, client):
        resp = client.get(
            "/api/admin/dashboard/trends?from_date=2026-01-01&to_date=2026-01-31"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "daily_active_users" in data
        assert "daily_sessions" in data
        assert "daily_tokens" in data
        assert isinstance(data["daily_active_users"], list)
        assert isinstance(data["daily_sessions"], list)
        assert isinstance(data["daily_tokens"], list)

    def test_trends_empty_for_no_data(self, client):
        resp = client.get(
            "/api/admin/dashboard/trends?from_date=2020-01-01&to_date=2020-01-31"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["daily_active_users"] == []
        assert data["daily_sessions"] == []
        assert data["daily_tokens"] == []

    def test_trends_date_item_structure(self, client):
        resp = client.get(
            "/api/admin/dashboard/trends?from_date=2026-01-01&to_date=2026-01-31"
        )
        assert resp.status_code == 200
        data = resp.json()
        for item in data["daily_active_users"]:
            assert "date" in item
            assert "count" in item
        for item in data["daily_sessions"]:
            assert "date" in item
            assert "count" in item
        for item in data["daily_tokens"]:
            assert "date" in item
            assert "input" in item
            assert "output" in item
            assert "cache_read" in item
            assert "cache_write" in item


class TestDashboardRankings:
    def test_rankings_returns_expected_structure(self, client):
        resp = client.get(
            "/api/admin/dashboard/rankings?from_date=2026-01-01&to_date=2026-01-31"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "top_users" in data
        assert "top_skills" in data
        assert isinstance(data["top_users"], list)
        assert isinstance(data["top_skills"], list)

    def test_rankings_empty_for_no_data(self, client):
        resp = client.get(
            "/api/admin/dashboard/rankings?from_date=2020-01-01&to_date=2020-01-31"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["top_users"] == []
        assert data["top_skills"] == []

    def test_rankings_user_item_structure(self, client):
        resp = client.get(
            "/api/admin/dashboard/rankings?from_date=2026-01-01&to_date=2026-01-31"
        )
        assert resp.status_code == 200
        data = resp.json()
        for user in data["top_users"]:
            assert "user_id" in user
            assert "total_tokens" in user
            assert "session_count" in user

    def test_rankings_skill_item_structure(self, client):
        resp = client.get(
            "/api/admin/dashboard/rankings?from_date=2026-01-01&to_date=2026-01-31"
        )
        assert resp.status_code == 200
        data = resp.json()
        for skill in data["top_skills"]:
            assert "skill_name" in skill
            assert "use_count" in skill
            assert "unique_users" in skill

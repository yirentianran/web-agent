"""Tests for evolution admin APIs."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

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

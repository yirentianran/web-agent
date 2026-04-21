"""Tests for session fork functionality."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main_server import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def data_dir(tmp_path) -> str:
    """Return a temporary data directory path."""
    d = tmp_path / "data"
    d.mkdir()
    return str(d)


class TestSessionFork:
    def test_fork_nonexistent_session(
        self, client: TestClient, data_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Forking a non-existent session still creates new buffer entry."""
        import main_server
        monkeypatch.setattr(main_server, "DATA_ROOT", data_dir)

        resp = client.post("/api/users/alice/sessions/nonexistent/fork")
        # Should still create a new buffer session even without prior history
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["forked_from"] == "nonexistent"
        assert data["session_id"].startswith("session_alice_")

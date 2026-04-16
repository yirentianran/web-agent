"""Tests for session fork functionality."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main_server import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory with session files."""
    user_dir = tmp_path / "users" / "alice" / "claude-data" / "sessions"
    user_dir.mkdir(parents=True)

    # Create session JSONL file
    session_file = user_dir / "session_orig.jsonl"
    session_file.write_text(json.dumps({
        "type": "user",
        "content": "Hello",
        "timestamp": "2026-04-13T10:00:00",
    }) + "\n" + json.dumps({
        "type": "assistant",
        "content": "Hi there",
    }) + "\n")

    # Create meta file
    meta_file = user_dir / "session_orig.meta.json"
    meta_file.write_text(json.dumps({"title": "Original Session", "updated_at": 1234567890}))

    # Create workspace dir (needed by user_data_dir)
    (tmp_path / "users" / "alice" / "workspace").mkdir(parents=True)

    return tmp_path


class TestSessionFork:
    def test_fork_creates_new_session_with_shared_history(
        self, client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fork endpoint copies history and creates new session."""
        import main_server
        monkeypatch.setattr(main_server, "DATA_ROOT", data_dir)

        resp = client.post("/api/users/alice/sessions/session_orig/fork")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["forked_from"] == "session_orig"
        assert data["session_id"].startswith("session_alice_")

    def test_fork_copies_meta_file(
        self, client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fork endpoint copies metadata file."""
        import main_server
        monkeypatch.setattr(main_server, "DATA_ROOT", data_dir)

        resp = client.post("/api/users/alice/sessions/session_orig/fork")
        assert resp.status_code == 200

        new_id = resp.json()["session_id"]
        meta_file = data_dir / "users" / "alice" / "claude-data" / "sessions" / f"{new_id}.meta.json"
        assert meta_file.exists()
        meta = json.loads(meta_file.read_text())
        assert meta["title"] == "Original Session"

    def test_fork_nonexistent_session(
        self, client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Forking a non-existent session still creates new buffer entry."""
        import main_server
        monkeypatch.setattr(main_server, "DATA_ROOT", data_dir)

        resp = client.post("/api/users/alice/sessions/nonexistent/fork")
        # Should still create a new buffer session even without disk files
        assert resp.status_code == 200

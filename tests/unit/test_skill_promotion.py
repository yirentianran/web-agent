"""Unit tests for skill promotion (personal -> shared)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main_server import app


@pytest.fixture()
def client(tmp_path: Path):
    """Create a test client with an isolated data directory."""
    data_root = tmp_path / "data"
    data_root.mkdir()
    import main_server

    original = main_server.DATA_ROOT
    main_server.DATA_ROOT = data_root
    try:
        with TestClient(app) as c:
            yield c
    finally:
        main_server.DATA_ROOT = original


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def _upload_skill(client: TestClient, user_id: str, skill_name: str) -> None:
    zip_bytes = _make_zip({"SKILL.md": f"# {skill_name}\n\nTest skill.\n".encode()})
    files = {"file": (f"{skill_name}.zip", io.BytesIO(zip_bytes), "application/zip")}
    resp = client.post(f"/api/users/{user_id}/skills/upload", files=files)
    assert resp.status_code == 200, f"Upload failed: {resp.text}"


class TestPromoteSkill:
    def test_promote_copies_directly_to_shared(self, client: TestClient):
        """Promote copies skill directly to shared-skills/ (no pending step)."""
        import main_server

        _upload_skill(client, "alice", "debug-tips")
        resp = client.post("/api/users/alice/skills/debug-tips/promote")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "promoted" in data["message"].lower()

        shared_dir = main_server.DATA_ROOT / "shared-skills" / "debug-tips"
        assert shared_dir.exists()
        assert (shared_dir / "SKILL.md").exists()
        assert (shared_dir / "skill-meta.json").exists()

        meta = json.loads((shared_dir / "skill-meta.json").read_text())
        assert meta["promoted_by"] == "alice"
        assert "promoted_at" in meta
        assert meta.get("source") in ("promoted", "upload")

    def test_promote_preserves_all_skill_files(self, client: TestClient):
        import main_server

        zip_bytes = _make_zip({
            "SKILL.md": b"# Test\n\nContent.\n",
            "helper.py": b"def run(): pass\n",
            "data/config.json": b'{"key": "value"}\n',
        })
        files = {"file": ("rich-skill.zip", io.BytesIO(zip_bytes), "application/zip")}
        resp = client.post("/api/users/alice/skills/upload", files=files)
        assert resp.status_code == 200

        resp = client.post("/api/users/alice/skills/rich-skill/promote")
        assert resp.status_code == 200

        shared_dir = main_server.DATA_ROOT / "shared-skills" / "rich-skill"
        assert (shared_dir / "SKILL.md").exists()
        assert (shared_dir / "helper.py").exists()
        assert (shared_dir / "data" / "config.json").exists()


class TestPromoteErrors:
    def test_promote_nonexistent_skill_returns_404(self, client: TestClient):
        resp = client.post("/api/users/alice/skills/no-such-skill/promote")
        assert resp.status_code == 404

    def test_promote_name_conflict_returns_409(self, client: TestClient):
        """If shared skill with same name exists, return 409."""
        import main_server

        existing = main_server.DATA_ROOT / "shared-skills" / "conflict-skill"
        existing.mkdir(parents=True)
        (existing / "SKILL.md").write_text(
            "---\nname: conflict-skill\ndescription: Already shared.\n---\n\n# Existing\n"
        )
        (existing / "skill-meta.json").write_text('{"source": "upload"}')

        _upload_skill(client, "alice", "conflict-skill")
        resp = client.post("/api/users/alice/skills/conflict-skill/promote")

        assert resp.status_code == 409
        detail = json.loads(resp.json()["detail"])
        assert detail["conflict_type"] == "name_conflict"
        assert detail["skill_name"] == "conflict-skill"

    def test_promote_invalid_user_skill_not_found(self, client: TestClient):
        """Different user's skill is not accessible."""
        _upload_skill(client, "alice", "private-tip")
        resp = client.post("/api/users/bob/skills/private-tip/promote")
        assert resp.status_code == 404

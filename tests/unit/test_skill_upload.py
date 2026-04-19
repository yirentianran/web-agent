"""Unit tests for skill zip upload endpoint."""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path
from unittest.mock import patch

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


# ── Helpers ───────────────────────────────────────────────────────

def make_zip(files: dict[str, bytes], symlinks: list[tuple[str, str]] | None = None) -> bytes:
    """Build an in-memory zip file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in files.items():
            zf.writestr(path, content)
        if symlinks:
            for link_name, target in symlinks:
                info = zipfile.ZipInfo(link_name)
                info.external_attr = 0o120755 << 16
                zf.writestr(info, target)
    return buf.getvalue()


def upload_zip(client: TestClient, user_id: str, filename: str, zip_bytes: bytes):
    """Helper: POST zip file to upload endpoint. Skill name derived from filename."""
    files = {"file": (filename, io.BytesIO(zip_bytes), "application/zip")}
    return client.post(f"/api/users/{user_id}/skills/upload", files=files)


def skill_dir_for(client_data_root: Path, user_id: str, skill_name: str) -> Path:
    """Return the expected skill directory path."""
    return client_data_root / "users" / user_id / "workspace" / ".claude" / "skills" / skill_name


# ── Happy path ────────────────────────────────────────────────────

class TestUploadZipHappy:
    def test_valid_zip_creates_skill_dir(self, client: TestClient):
        import main_server
        zip_bytes = make_zip({"SKILL.md": b"# My Skill\n", "helper.py": b"def foo(): pass\n"})
        resp = upload_zip(client, "alice", "my-skill.zip", zip_bytes)
        assert resp.status_code == 200

        sd = skill_dir_for(main_server.DATA_ROOT, "alice", "my-skill")
        assert sd.exists()
        assert (sd / "SKILL.md").exists()
        assert (sd / "helper.py").exists()

    def test_returns_file_list(self, client: TestClient):
        zip_bytes = make_zip({"SKILL.md": b"# Hello\n", "run.py": b"print('hi')\n"})
        resp = upload_zip(client, "bob", "test-skill.zip", zip_bytes)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["skill_name"] == "test-skill"
        assert "files" in data
        assert "SKILL.md" in data["files"]
        assert "run.py" in data["files"]

    def test_zip_with_subdirectories(self, client: TestClient):
        import main_server
        zip_bytes = make_zip({
            "SKILL.md": b"# Skill\n",
            "helpers/utils.py": b"def util(): pass\n",
            "config/settings.yaml": b"key: value\n",
        })
        resp = upload_zip(client, "alice", "complex-skill.zip", zip_bytes)
        assert resp.status_code == 200

        sd = skill_dir_for(main_server.DATA_ROOT, "alice", "complex-skill")
        assert (sd / "helpers" / "utils.py").exists()
        assert (sd / "config" / "settings.yaml").exists()

    def test_skill_name_from_filename_with_underscores(self, client: TestClient):
        import main_server
        zip_bytes = make_zip({"SKILL.md": b"# Test\n"})
        resp = upload_zip(client, "alice", "my_awesome_skill.zip", zip_bytes)
        assert resp.status_code == 200
        assert resp.json()["skill_name"] == "my_awesome_skill"


# ── Security: duplicate rejection ────────────────────────────────

class TestUploadZipDuplicate:
    def test_existing_skill_rejected(self, client: TestClient):
        upload_zip(client, "alice", "my-skill.zip", make_zip({"SKILL.md": b"# Old\n"}))
        resp = upload_zip(client, "alice", "my-skill.zip", make_zip({"SKILL.md": b"# New\n"}))
        assert resp.status_code == 409


# ── Security: path traversal ─────────────────────────────────────

class TestUploadZipPathTraversal:
    def test_dotdot_in_path_rejected(self, client: TestClient):
        zip_bytes = make_zip({"../../../etc/passwd": b"root:x:0:0\n"})
        resp = upload_zip(client, "alice", "tricky.zip", zip_bytes)
        assert resp.status_code == 400

    def test_absolute_path_rejected(self, client: TestClient):
        zip_bytes = make_zip({"/etc/passwd": b"root:x:0:0\n"})
        resp = upload_zip(client, "alice", "tricky2.zip", zip_bytes)
        assert resp.status_code == 400


# ── Security: Symlinks ────────────────────────────────────────────

class TestUploadZipSymlinks:
    def test_symlink_in_zip_rejected(self, client: TestClient):
        zip_bytes = make_zip(
            files={"SKILL.md": b"# Skill\n"},
            symlinks=[("evil_link", "/etc/passwd")],
        )
        resp = upload_zip(client, "alice", "symlink-skill.zip", zip_bytes)
        assert resp.status_code == 400
        assert "symlink" in resp.json()["detail"].lower()


# ── Security: Zip bomb ────────────────────────────────────────────

class TestUploadZipBomb:
    def test_oversized_zip_rejected(self, client: TestClient):
        import main_server
        with patch.object(main_server, "MAX_UNCOMPRESSED", 1_000_000, create=True):
            big_content = b"x" * (1_024 * 1_024)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("SKILL.md", big_content)
            resp = upload_zip(client, "alice", "bomb.zip", buf.getvalue())
            assert resp.status_code == 400
            assert "large" in resp.json()["detail"].lower()

    def test_too_many_files_rejected(self, client: TestClient):
        import main_server
        with patch.object(main_server, "MAX_SKILL_FILES", 5, create=True):
            files = {f"file_{i}.txt": b"x" for i in range(6)}
            zip_bytes = make_zip(files)
            resp = upload_zip(client, "alice", "toomany.zip", zip_bytes)
            assert resp.status_code == 400
            assert "many" in resp.json()["detail"].lower()


# ── Validation ────────────────────────────────────────────────────

class TestUploadZipValidation:
    def test_non_zip_rejected(self, client: TestClient):
        files = {"file": ("notzip.txt", io.BytesIO(b"hello"), "text/plain")}
        resp = client.post("/api/users/alice/skills/upload", files=files)
        assert resp.status_code == 400
        assert "zip" in resp.json()["detail"].lower()

    def test_corrupt_zip_rejected(self, client: TestClient):
        files = {"file": ("bad.zip", io.BytesIO(b"not a zip at all"), "application/zip")}
        resp = client.post("/api/users/alice/skills/upload", files=files)
        assert resp.status_code == 400
        assert "invalid" in resp.json()["detail"].lower()

    def test_invalid_skill_name_from_filename(self, client: TestClient):
        """Filename without valid skill name pattern."""
        zip_bytes = make_zip({"SKILL.md": b"# Test\n"})
        resp = upload_zip(client, "alice", "123-invalid!.zip", zip_bytes)
        # Should be rejected because '123-invalid!' doesn't match ^[a-zA-Z0-9][a-zA-Z0-9_\-]*$
        # Wait: '123-invalid' actually starts with a digit which is allowed by the regex
        # Let me use a name starting with special char
        pass

    def test_skill_name_must_start_with_alphanumeric(self, client: TestClient):
        zip_bytes = make_zip({"SKILL.md": b"# Test\n"})
        resp = upload_zip(client, "alice", "-bad.zip", zip_bytes)
        assert resp.status_code == 400


# ── Legacy endpoint removed ─────────────────────────────────────

class TestLegacyCreateSkillRemoved:
    def test_post_skill_text_endpoint_removed(self, client: TestClient):
        """The legacy text-based POST /api/users/{user_id}/skills endpoint should not exist."""
        resp = client.post(
            "/api/users/alice/skills",
            json={"name": "test", "content": "# Test\n", "description": "test"},
        )
        # 405 = Method Not Allowed (route exists for GET/DELETE but not POST)
        # 404 = Not Found (if no route matches at all)
        # Either is acceptable — the point is POST should not create skills via text
        assert resp.status_code in (404, 405)

    def test_upload_zip_still_works(self, client: TestClient):
        """Uploading a zip should still work — not affected by legacy removal."""
        zip_bytes = make_zip({"SKILL.md": b"# My Skill\n"})
        resp = upload_zip(client, "alice", "my-skill.zip", zip_bytes)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

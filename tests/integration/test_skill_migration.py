import pytest
import json
from pathlib import Path
from src.skill_manager import SkillManager
from src.database import Database


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    db = Database(db_path=db_path)
    await db.init()
    yield db
    await db.close()


@pytest.fixture
def data_root(tmp_path):
    """Create a fake data root with some skills."""
    root = tmp_path / "data"
    shared = root / "shared-skills" / "code-review"
    shared.mkdir(parents=True)
    (shared / "SKILL.md").write_text("# Code Review Skill")
    meta = shared / "skill-meta.json"
    meta.write_text(json.dumps({"owner": "admin", "source": "shared"}))

    # Personal skill
    personal = root / "users" / "user1" / "workspace" / ".claude" / "skills" / "my-skill"
    personal.mkdir(parents=True)
    (personal / "SKILL.md").write_text("# My Skill")

    return root


@pytest.mark.asyncio
async def test_migrate_from_filesystem(db, data_root, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(data_root))
    mgr = SkillManager(db=db)
    result = await mgr.migrate_from_filesystem()
    assert result["registered"] == 2
    skill = await mgr.get_skill("code-review")
    assert skill is not None
    assert skill["source"] == "shared"
    assert skill["owner_id"] == "admin"
    personal = await mgr.get_skill("my-skill")
    assert personal is not None
    assert personal["source"] == "personal"
    assert personal["owner_id"] == "user1"


@pytest.mark.asyncio
async def test_migrate_legacy_file_versions(db, tmp_path, monkeypatch):
    """SKILL_v*.md files get converted to @vN directories."""
    root = tmp_path / "data"
    shared = root / "shared-skills" / "test-skill"
    shared.mkdir(parents=True)
    (shared / "SKILL.md").write_text("# Current")
    (shared / "SKILL_v1.md").write_text("# Version 1")
    (shared / "SKILL_v2.md").write_text("# Version 2")

    monkeypatch.setenv("DATA_ROOT", str(root))
    mgr = SkillManager(db=db)
    result = await mgr.migrate_from_filesystem()
    assert result["versions_migrated"] == 2
    assert (shared.with_name("test-skill@v1") / "SKILL.md").exists()
    assert (shared.with_name("test-skill@v2") / "SKILL.md").exists()
    assert not (shared / "SKILL_v1.md").exists()


@pytest.mark.asyncio
async def test_migrate_legacy_versions_dirs(db, tmp_path, monkeypatch):
    """versions/vN/ dirs get converted to @vN directories."""
    root = tmp_path / "data"
    shared = root / "shared-skills" / "test-skill"
    shared.mkdir(parents=True)
    (shared / "SKILL.md").write_text("# Current")
    v1 = shared / "versions" / "v1"
    v1.mkdir(parents=True)
    (v1 / "SKILL.md").write_text("# V1")
    v2 = shared / "versions" / "v2"
    v2.mkdir(parents=True)
    (v2 / "SKILL.md").write_text("# V2")

    monkeypatch.setenv("DATA_ROOT", str(root))
    mgr = SkillManager(db=db)
    result = await mgr.migrate_from_filesystem()
    assert result["versions_migrated"] == 2
    assert (shared.with_name("test-skill@v1") / "SKILL.md").exists()
    assert (shared.with_name("test-skill@v2") / "SKILL.md").exists()
    assert not (shared / "versions" / "v1").exists()


@pytest.mark.asyncio
async def test_skip_already_registered(db, data_root, monkeypatch):
    """Running migration twice doesn't duplicate skills."""
    monkeypatch.setenv("DATA_ROOT", str(data_root))
    mgr = SkillManager(db=db)
    first = await mgr.migrate_from_filesystem()
    assert first["registered"] == 2
    second = await mgr.migrate_from_filesystem()
    assert second["registered"] == 0


@pytest.mark.asyncio
async def test_migrate_with_no_skills_dir(db, tmp_path, monkeypatch):
    """Migration handles missing directories gracefully."""
    root = tmp_path / "data"
    root.mkdir(parents=True)
    monkeypatch.setenv("DATA_ROOT", str(root))
    mgr = SkillManager(db=db)
    result = await mgr.migrate_from_filesystem()
    assert result["registered"] == 0
    assert result["versions_migrated"] == 0

"""Unit tests for SkillManager CRUD, usage tracking, and version management."""

from __future__ import annotations

import pytest
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


@pytest.mark.asyncio
async def test_register_skill(db):
    mgr = SkillManager(db=db)
    await mgr.register_skill(
        skill_name="test-skill",
        source="personal",
        owner_id="user1",
        description="A test skill",
        category="coding",
        tags=["python", "testing"],
    )
    skill = await mgr.get_skill("test-skill")
    assert skill is not None
    assert skill["skill_name"] == "test-skill"
    assert skill["owner_id"] == "user1"
    assert skill["category"] == "coding"
    assert "python" in skill["tags"]


@pytest.mark.asyncio
async def test_list_skills_by_category(db):
    mgr = SkillManager(db=db)
    await mgr.register_skill("py-skill", source="personal", owner_id="u1", category="coding")
    await mgr.register_skill("data-skill", source="personal", owner_id="u1", category="data")
    coding = await mgr.list_skills(category="coding")
    assert len(coding) == 1
    assert coding[0]["skill_name"] == "py-skill"


@pytest.mark.asyncio
async def test_list_skills_by_tag(db):
    mgr = SkillManager(db=db)
    await mgr.register_skill("py-skill", source="personal", owner_id="u1", tags=["python", "testing"])
    await mgr.register_skill("js-skill", source="personal", owner_id="u1", tags=["javascript"])
    result = await mgr.list_skills(tag="python")
    assert len(result) == 1
    assert result[0]["skill_name"] == "py-skill"


@pytest.mark.asyncio
async def test_record_usage_and_stats(db):
    mgr = SkillManager(db=db)
    await mgr.register_skill("test-skill", source="shared", owner_id="")
    await mgr.record_usage("test-skill", user_id="u1", session_id="s1", version_number=2)
    await mgr.record_usage("test-skill", user_id="u2", session_id="s2", version_number=2)
    await mgr.record_usage("test-skill", user_id="u1", session_id="s3", version_number=1)
    stats = await mgr.get_usage_stats("test-skill")
    assert stats["total_uses"] == 3
    assert stats["unique_users"] == 2
    assert len(stats["version_breakdown"]) == 2
    assert stats["version_breakdown"][0]["version"] == 2


@pytest.mark.asyncio
async def test_record_usage_fire_and_forget(db):
    """record_usage never raises, even with bad data."""
    mgr = SkillManager(db=db)
    await mgr.record_usage("nonexistent-skill", user_id="u1", session_id="s1")
    await mgr.record_usage("test-skill", version_number=-1, action="invalid")


@pytest.mark.asyncio
async def test_version_lifecycle(db, tmp_path):
    mgr = SkillManager(db=db)
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Initial version")
    await mgr.register_skill("test-skill", source="shared", owner_id="", path=str(skill_dir))

    v1_dir = tmp_path / "test-skill@v1"
    v1_dir.mkdir()
    (v1_dir / "SKILL.md").write_text("# Version 1")
    await mgr.record_version("test-skill", 1, path=str(v1_dir), change_summary="Initial", created_by="upload")

    v2_dir = tmp_path / "test-skill@v2"
    v2_dir.mkdir()
    (v2_dir / "SKILL.md").write_text("# Version 2")
    await mgr.record_version("test-skill", 2, path=str(v2_dir), change_summary="Updated", created_by="agent")

    versions = await mgr.list_versions("test-skill")
    assert len(versions) == 2
    assert versions[0]["version_number"] == 2

    result = await mgr.activate_version("test-skill", 2)
    assert result is not None
    assert result["activated"] is True
    assert (skill_dir / "SKILL.md").read_text() == "# Version 2"
    skill = await mgr.get_skill("test-skill")
    assert skill["version"] == "v2"


@pytest.mark.asyncio
async def test_get_top_skills(db):
    mgr = SkillManager(db=db)
    await mgr.register_skill("skill-a", source="shared", owner_id="")
    await mgr.register_skill("skill-b", source="shared", owner_id="")
    for _ in range(5):
        await mgr.record_usage("skill-a", user_id="u1")
    for _ in range(3):
        await mgr.record_usage("skill-b", user_id="u1")
    top = await mgr.get_top_skills()
    assert top[0]["skill_name"] == "skill-a"
    assert top[0]["uses"] == 5


@pytest.mark.asyncio
async def test_register_skill_idempotent(db):
    mgr = SkillManager(db=db)
    await mgr.register_skill("test-skill", source="personal", owner_id="u1")
    await mgr.register_skill("test-skill", source="shared", owner_id="u2")
    skill = await mgr.get_skill("test-skill")
    assert skill is not None
    assert skill["source"] == "shared"
    assert skill["owner_id"] == "u2"


@pytest.mark.asyncio
async def test_delete_skill_deprecated_status(db):
    mgr = SkillManager(db=db)
    await mgr.register_skill("test-skill", source="personal", owner_id="u1")
    await mgr.delete_skill("test-skill")
    skill = await mgr.get_skill("test-skill")
    assert skill is not None
    assert skill["status"] == "deprecated"


@pytest.mark.asyncio
async def test_update_skill_meta(db):
    mgr = SkillManager(db=db)
    await mgr.register_skill("test-skill", source="personal", owner_id="u1")
    await mgr.update_skill_meta("test-skill", category="coding", tags=["python"], description="Updated")
    skill = await mgr.get_skill("test-skill")
    assert skill["category"] == "coding"
    assert skill["tags"] == ["python"]
    assert skill["description"] == "Updated"


@pytest.mark.asyncio
async def test_list_skills_no_filter(db):
    mgr = SkillManager(db=db)
    await mgr.register_skill("a", source="personal", owner_id="u1")
    await mgr.register_skill("b", source="shared", owner_id="")
    all_skills = await mgr.list_skills()
    assert len(all_skills) == 2


@pytest.mark.asyncio
async def test_get_skill_not_found(db):
    mgr = SkillManager(db=db)
    result = await mgr.get_skill("nonexistent")
    assert result is None

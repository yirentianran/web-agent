"""Tests for semi-automatic skill evolution: preview, activate, rollback, version history."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from src.database import Database


def _init_db(tmp_path: Path) -> Database:
    db = Database(db_path=tmp_path / "test.db")
    asyncio.get_event_loop().run_until_complete(db.init())
    return db


def _make_manager(db: Database) -> "DBSkillFeedbackManager":
    from src.skill_feedback import DBSkillFeedbackManager
    return DBSkillFeedbackManager(db=db)


class MockProcess:
    """Mock subprocess for claude CLI."""
    returncode = 0

    async def communicate(self):
        return (b"# Test Skill\nImproved content.", b"")


# ── Test: Preview Evolution (generate without activating) ───────


class TestPreviewEvolution:
    """Preview evolution should generate SKILL_vN.md but NOT replace SKILL.md."""

    def test_preview_creates_version_without_activating(self, tmp_path: Path) -> None:
        """Preview should create SKILL_v1.md but keep SKILL.md unchanged."""
        db = _init_db(tmp_path)
        try:
            mgr = _make_manager(db)
            loop = asyncio.get_event_loop()

            # Add feedback to trigger evolution
            for i in range(12):
                loop.run_until_complete(
                    mgr.submit_feedback("test-skill", user_id=f"user{i}", rating=2, comment=f"Bad {i}")
                )

            skill_dir = tmp_path / "skills" / "test-skill"
            skill_dir.mkdir(parents=True)
            original_content = "# Test Skill\nOriginal content."
            (skill_dir / "SKILL.md").write_text(original_content)

            with patch("shutil.which", return_value="/usr/bin/claude"):
                with patch("asyncio.create_subprocess_exec", return_value=MockProcess()):
                    result = loop.run_until_complete(
                        mgr.preview_evolution("test-skill", skills_dir=tmp_path / "skills")
                    )

            # Preview should succeed
            assert result is not None
            assert result["version_number"] == 1
            assert result["activated"] is False

            # SKILL.md should be UNCHANGED
            assert (skill_dir / "SKILL.md").read_text() == original_content

            # SKILL_v1.md should exist with new content
            assert (skill_dir / "SKILL_v1.md").exists()
            assert (skill_dir / "SKILL_v1.md").read_text() == "# Test Skill\nImproved content."
        finally:
            asyncio.get_event_loop().run_until_complete(db.close())

    def test_preview_returns_none_when_no_skill_file(self, tmp_path: Path) -> None:
        """Preview should return None if SKILL.md doesn't exist."""
        db = _init_db(tmp_path)
        try:
            mgr = _make_manager(db)
            loop = asyncio.get_event_loop()

            result = loop.run_until_complete(
                mgr.preview_evolution("missing-skill", skills_dir=tmp_path / "skills")
            )
            assert result is None
        finally:
            asyncio.get_event_loop().run_until_complete(db.close())

    def test_preview_returns_none_when_no_feedback(self, tmp_path: Path) -> None:
        """Preview should return None if there's no feedback to drive evolution."""
        db = _init_db(tmp_path)
        try:
            mgr = _make_manager(db)
            loop = asyncio.get_event_loop()

            skill_dir = tmp_path / "skills" / "test-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Test")

            with patch("shutil.which", return_value="/usr/bin/claude"):
                result = loop.run_until_complete(
                    mgr.preview_evolution("test-skill", skills_dir=tmp_path / "skills")
                )
            assert result is None
        finally:
            asyncio.get_event_loop().run_until_complete(db.close())


# ── Test: Activate Version ─────────────────────────────────────


class TestActivateVersion:
    """Activate a specific version, replacing SKILL.md."""

    def test_activate_existing_version(self, tmp_path: Path) -> None:
        """Activating a version should replace SKILL.md and backup the old one."""
        db = _init_db(tmp_path)
        try:
            mgr = _make_manager(db)
            loop = asyncio.get_event_loop()

            skill_dir = tmp_path / "skills" / "test-skill"
            skill_dir.mkdir(parents=True)
            original = "# Test Skill\nOriginal."
            (skill_dir / "SKILL.md").write_text(original)
            (skill_dir / "SKILL_v1.md").write_text("# Test Skill\nVersion 1.")

            result = loop.run_until_complete(
                mgr.activate_version("test-skill", version_number=1, skills_dir=tmp_path / "skills")
            )

            assert result is not None
            assert result["activated"] is True
            assert result["version_number"] == 1
            assert result["backup"] is not None

            # SKILL.md should now contain version 1 content
            assert (skill_dir / "SKILL.md").read_text() == "# Test Skill\nVersion 1."

            # Backup should exist
            backup_path = skill_dir / result["backup"]
            assert backup_path.exists()
            assert backup_path.read_text() == original
        finally:
            asyncio.get_event_loop().run_until_complete(db.close())

    def test_activate_nonexistent_version(self, tmp_path: Path) -> None:
        """Activating a non-existent version should return None."""
        db = _init_db(tmp_path)
        try:
            mgr = _make_manager(db)
            loop = asyncio.get_event_loop()

            skill_dir = tmp_path / "skills" / "test-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Test")

            result = loop.run_until_complete(
                mgr.activate_version("test-skill", version_number=99, skills_dir=tmp_path / "skills")
            )
            assert result is None
        finally:
            asyncio.get_event_loop().run_until_complete(db.close())

    def test_activate_same_version_noop(self, tmp_path: Path) -> None:
        """Activating the version that is already active should return a clean result."""
        db = _init_db(tmp_path)
        try:
            mgr = _make_manager(db)
            loop = asyncio.get_event_loop()

            skill_dir = tmp_path / "skills" / "test-skill"
            skill_dir.mkdir(parents=True)
            version_content = "# Test Skill\nVersion 1."
            (skill_dir / "SKILL.md").write_text(version_content)
            (skill_dir / "SKILL_v1.md").write_text(version_content)

            result = loop.run_until_complete(
                mgr.activate_version("test-skill", version_number=1, skills_dir=tmp_path / "skills")
            )

            # Should still succeed (content is the same)
            assert result is not None
            assert result["activated"] is True
        finally:
            asyncio.get_event_loop().run_until_complete(db.close())


# ── Test: Rollback ─────────────────────────────────────────────


class TestRollback:
    """Rollback to a previous version."""

    def test_rollback_restores_latest_backup(self, tmp_path: Path) -> None:
        """Rollback should restore the most recent backup version."""
        db = _init_db(tmp_path)
        try:
            mgr = _make_manager(db)
            loop = asyncio.get_event_loop()

            skill_dir = tmp_path / "skills" / "test-skill"
            skill_dir.mkdir(parents=True)
            original = "# Test Skill\nOriginal."
            (skill_dir / "SKILL.md").write_text("# Test Skill\nV2 content.")
            (skill_dir / "SKILL_backup_v1.md").write_text(original)

            result = loop.run_until_complete(
                mgr.rollback_version("test-skill", skills_dir=tmp_path / "skills")
            )

            assert result is not None
            assert result["rolled_back"] is True
            assert (skill_dir / "SKILL.md").read_text() == original
        finally:
            asyncio.get_event_loop().run_until_complete(db.close())

    def test_rollback_no_backup(self, tmp_path: Path) -> None:
        """Rollback should return None if no backup exists."""
        db = _init_db(tmp_path)
        try:
            mgr = _make_manager(db)
            loop = asyncio.get_event_loop()

            skill_dir = tmp_path / "skills" / "test-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Test")

            result = loop.run_until_complete(
                mgr.rollback_version("test-skill", skills_dir=tmp_path / "skills")
            )
            assert result is None
        finally:
            asyncio.get_event_loop().run_until_complete(db.close())


# ── Test: Version History ──────────────────────────────────────


class TestVersionHistory:
    """List all versions of a skill with metadata."""

    def test_list_versions_empty(self, tmp_path: Path) -> None:
        """Should return empty list when no versions exist."""
        db = _init_db(tmp_path)
        try:
            mgr = _make_manager(db)
            loop = asyncio.get_event_loop()

            skill_dir = tmp_path / "skills" / "test-skill"
            skill_dir.mkdir(parents=True)

            versions = loop.run_until_complete(
                mgr.list_versions("test-skill", skills_dir=tmp_path / "skills")
            )
            assert versions == []
        finally:
            asyncio.get_event_loop().run_until_complete(db.close())

    def test_list_versions_with_files(self, tmp_path: Path) -> None:
        """Should list all versions found on disk."""
        db = _init_db(tmp_path)
        try:
            mgr = _make_manager(db)
            loop = asyncio.get_event_loop()

            skill_dir = tmp_path / "skills" / "test-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Current")
            (skill_dir / "SKILL_v1.md").write_text("# V1")
            (skill_dir / "SKILL_v2.md").write_text("# V2")
            (skill_dir / "SKILL_backup_v1.md").write_text("# Backup V1")

            versions = loop.run_until_complete(
                mgr.list_versions("test-skill", skills_dir=tmp_path / "skills")
            )

            assert len(versions) == 4
            names = [v["name"] for v in versions]
            assert "SKILL" in names  # SKILL.md -> "SKILL"
            assert "SKILL_v1" in names
            assert "SKILL_v2" in names
            assert "SKILL_backup_v1" in names
        finally:
            asyncio.get_event_loop().run_until_complete(db.close())

    def test_get_version_content(self, tmp_path: Path) -> None:
        """Should return content of a specific version file."""
        db = _init_db(tmp_path)
        try:
            mgr = _make_manager(db)
            loop = asyncio.get_event_loop()

            skill_dir = tmp_path / "skills" / "test-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Current")
            (skill_dir / "SKILL_v1.md").write_text("# Version 1")

            content = loop.run_until_complete(
                mgr.get_version_content("test-skill", "SKILL_v1", skills_dir=tmp_path / "skills")
            )
            assert content == "# Version 1"
        finally:
            asyncio.get_event_loop().run_until_complete(db.close())

    def test_get_version_content_not_found(self, tmp_path: Path) -> None:
        """Should return None for non-existent version."""
        db = _init_db(tmp_path)
        try:
            mgr = _make_manager(db)
            loop = asyncio.get_event_loop()

            skill_dir = tmp_path / "skills" / "test-skill"
            skill_dir.mkdir(parents=True)

            content = loop.run_until_complete(
                mgr.get_version_content("test-skill", "nonexistent", skills_dir=tmp_path / "skills")
            )
            assert content is None
        finally:
            asyncio.get_event_loop().run_until_complete(db.close())


# ── Test: SkillEvolutionManager DB-backed preview ──────────────


class TestSkillEvolutionManagerDBPreview:
    """Test SkillEvolutionManager's db_* methods for semi-auto evolution."""

    def _init_db(self, tmp_path: Path) -> Database:
        db = Database(db_path=tmp_path / "test.db")
        asyncio.get_event_loop().run_until_complete(db.init())
        return db

    def test_db_preview_evolution(self, tmp_path: Path) -> None:
        """DB-backed preview should call DBSkillFeedbackManager.preview_evolution."""
        from src.skill_evolution import SkillEvolutionManager
        from src.skill_feedback import DBSkillFeedbackManager

        db = self._init_db(tmp_path)
        try:
            loop = asyncio.get_event_loop()
            db_mgr = DBSkillFeedbackManager(db=db)
            for i in range(12):
                loop.run_until_complete(
                    db_mgr.submit_feedback("test", user_id=f"user{i}", rating=2, comment="Bad")
                )

            skill_dir = tmp_path / "skills" / "test"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Test\nContent")

            mgr = SkillEvolutionManager(db=db)

            with patch("shutil.which", return_value="/usr/bin/claude"):
                with patch("asyncio.create_subprocess_exec", return_value=MockProcess()):
                    result = loop.run_until_complete(
                        mgr.db_preview_evolution("test", skills_dir=tmp_path / "skills")
                    )

            assert result is not None
            assert result["activated"] is False
        finally:
            asyncio.get_event_loop().run_until_complete(db.close())

    def test_db_activate_version(self, tmp_path: Path) -> None:
        """DB-backed activate should call DBSkillFeedbackManager.activate_version."""
        from src.skill_evolution import SkillEvolutionManager

        db = self._init_db(tmp_path)
        try:
            loop = asyncio.get_event_loop()

            skill_dir = tmp_path / "skills" / "test"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Original")
            (skill_dir / "SKILL_v1.md").write_text("# Improved")

            mgr = SkillEvolutionManager(db=db)

            result = loop.run_until_complete(
                mgr.db_activate_version("test", version_number=1, skills_dir=tmp_path / "skills")
            )

            assert result is not None
            assert result["activated"] is True
            assert (skill_dir / "SKILL.md").read_text() == "# Improved"
        finally:
            asyncio.get_event_loop().run_until_complete(db.close())

    def test_db_rollback_version(self, tmp_path: Path) -> None:
        """DB-backed rollback should call DBSkillFeedbackManager.rollback_version."""
        from src.skill_evolution import SkillEvolutionManager

        db = self._init_db(tmp_path)
        try:
            loop = asyncio.get_event_loop()

            skill_dir = tmp_path / "skills" / "test"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# V2")
            (skill_dir / "SKILL_backup_v1.md").write_text("# V1")

            mgr = SkillEvolutionManager(db=db)

            result = loop.run_until_complete(
                mgr.db_rollback_version("test", skills_dir=tmp_path / "skills")
            )

            assert result is not None
            assert result["rolled_back"] is True
            assert (skill_dir / "SKILL.md").read_text() == "# V1"
        finally:
            asyncio.get_event_loop().run_until_complete(db.close())

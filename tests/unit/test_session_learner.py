"""Tests for session_learner."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_analyze_skips_short_sessions(db, tmp_path):
    from src.session_learner import SessionLearner
    learner = SessionLearner(db, tmp_path)
    result = await learner.analyze_session("nonexistent")
    assert result["skipped"] is True
    assert result["reason"] == "too_short"


@pytest.mark.asyncio
async def test_build_prompt_formats_messages(db, tmp_path):
    from src.session_learner import SessionLearner
    learner = SessionLearner(db, tmp_path)
    messages = [
        {"seq": 1, "type": "user", "name": None, "content": "Hello"},
        {"seq": 2, "type": "assistant", "name": None, "content": "Hi there"},
    ]
    prompt = learner._build_prompt(messages, ["code-reviewer"], {})
    assert "[1] user: Hello" in prompt
    assert "code-reviewer" in prompt


@pytest.mark.asyncio
async def test_parse_haiku_response_applies_high_confidence(db, tmp_path):
    from src.session_learner import SessionLearner
    from src.evolution_log import EvolutionLogStore

    store = EvolutionLogStore(db)
    learner = SessionLearner(db, tmp_path)

    # Setup: create a skill file to improve
    skill_dir = tmp_path / "shared-skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: test-skill\nversion: 1.0\n---\n\n# Old")

    imp = {
        "skill_name": "test-skill",
        "confidence": 8,
        "issue": "bad error handling",
        "suggested_fix": "---\nname: test-skill\nversion: 2.0\n---\n\n# Fixed",
    }

    await learner._apply_improvement(imp, "sess_1")

    # Verify new content was written
    content = (skill_dir / "SKILL.md").read_text()
    assert "# Fixed" in content

    # Verify evolution_log entry
    logs = await store.list_logs(skill_name="test-skill")
    assert logs["total"] == 1
    assert logs["items"][0]["source"] == "session_learner"


@pytest.mark.asyncio
async def test_create_learned_skill(db, tmp_path):
    from src.session_learner import SessionLearner
    learner = SessionLearner(db, tmp_path)

    pat = {
        "name": "debug-pattern",
        "confidence": 8,
        "description": "A reusable debugging technique",
        "skill_content": "---\nname: debug-pattern\ndescription: Debug helper\n---\n\n# Debug",
    }

    await learner._create_learned_skill(pat, "sess_1")

    skill_file = tmp_path / "shared-skills" / "debug-pattern" / "SKILL.md"
    assert skill_file.exists()
    content = skill_file.read_text()
    assert "Debug helper" in content


@pytest.mark.asyncio
async def test_extract_version_from_frontmatter():
    from src.session_learner import SessionLearner
    content = "---\nname: foo\nversion: 2.3\n---\n\n# Body"
    assert SessionLearner._extract_version(content) == "2.3"


@pytest.mark.asyncio
async def test_extract_version_default():
    from src.session_learner import SessionLearner
    assert SessionLearner._extract_version("# No frontmatter") == "1.0"

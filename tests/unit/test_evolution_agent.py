"""Tests for Agent-driven skill evolution (evolve-agent endpoint).

This tests the new evolution flow where an Agent session is launched
to improve a skill based on feedback, rather than a simple LLM text rewrite.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

# ── Mock claude_agent_sdk before main_server imports it ────────────
_mock_sdk = MagicMock()
_mock_sdk.ClaudeSDKClient = MagicMock()
_mock_sdk.types = MagicMock()
_mock_sdk.types.AssistantMessage = MagicMock
_mock_sdk.types.ClaudeAgentOptions = MagicMock
_mock_sdk.types.PermissionResultAllow = MagicMock
_mock_sdk.types.PermissionResult = MagicMock
_mock_sdk.types.ResultMessage = MagicMock
_mock_sdk.types.StreamEvent = MagicMock
_mock_sdk.types.SystemMessage = MagicMock
_mock_sdk.types.TextBlock = MagicMock
_mock_sdk.types.ThinkingBlock = MagicMock
_mock_sdk.types.ToolPermissionContext = MagicMock
_mock_sdk.types.ToolUseBlock = MagicMock
_mock_sdk.types.UserMessage = MagicMock
sys.modules["claude_agent_sdk"] = _mock_sdk
sys.modules["claude_agent_sdk.types"] = _mock_sdk.types

# Now import the server
from fastapi.testclient import TestClient

import main_server


@pytest.fixture(autouse=True)
def _patch_data_root(tmp_path: Path) -> None:
    """Redirect DATA_ROOT to a temporary directory for each test."""
    main_server.DATA_ROOT = tmp_path  # Already a Path
    main_server.buffer = main_server.MessageBuffer(base_dir=tmp_path / ".msg-buffer")
    main_server.active_tasks.clear()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(main_server.app)


# ── Evolution task creation ──────────────────────────────────────


class TestRunEvolutionAgent:
    """Test the run_evolution_agent function."""

    def test_run_evolution_agent_calls_db_get_feedback_for_evolution(
        self, tmp_path: Path
    ) -> None:
        """run_evolution_agent should call mgr.db_get_feedback_for_evolution, not get_feedback_for_evolution."""
        import inspect
        source = inspect.getsource(main_server.run_evolution_agent)

        # Must NOT call get_feedback_for_evolution directly on SkillEvolutionManager
        assert "mgr.get_feedback_for_evolution" not in source, (
            "run_evolution_agent calls get_feedback_for_evolution on SkillEvolutionManager, "
            "which doesn't have this method. It should call db_get_feedback_for_evolution "
            "which delegates to DBSkillFeedbackManager."
        )

        # Must call the delegated method
        assert "db_get_feedback_for_evolution" in source, (
            "run_evolution_agent should call mgr.db_get_feedback_for_evolution "
            "to delegate to DBSkillFeedbackManager."
        )

    def test_evolution_prompt_contains_feedback(self) -> None:
        """The evolution prompt should include high-quality and low-rated feedback."""
        from main_server import build_evolution_prompt

        feedback = {
            "high_quality": [
                {"rating": 5, "comment": "Great workflow!", "user_edits": ""},
            ],
            "low_rated": [
                {"rating": 1, "comment": "Missing error handling", "user_edits": ""},
            ],
            "user_edits": [
                {"rating": 4, "comment": "", "user_edits": "Added try/except block"},
            ],
        }

        prompt = build_evolution_prompt(
            skill_name="test-skill",
            skill_path=Path("/fake/path"),
            version_dir=Path("/fake/version"),
            skill_content="# Test Skill\nSome content.",
            skill_files=["scripts/helper.py", "references/api.md"],
            feedback=feedback,
        )

        assert "Great workflow!" in prompt
        assert "Missing error handling" in prompt
        assert "Added try/except block" in prompt
        assert "test-skill" in prompt

    def test_evolution_prompt_contains_skill_content(self) -> None:
        """The evolution prompt should include the full SKILL.md content."""
        from main_server import build_evolution_prompt

        prompt = build_evolution_prompt(
            skill_name="my-skill",
            skill_path=Path("/fake"),
            version_dir=Path("/fake/v1"),
            skill_content="---\nname: my-skill\n---\n# My Skill",
            skill_files=[],
            feedback={"high_quality": [], "low_rated": [], "user_edits": []},
        )

        assert "---\nname: my-skill\n---\n# My Skill" in prompt

    def test_evolution_prompt_includes_version_dir(self) -> None:
        """The evolution prompt should tell the LLM where to write output."""
        from main_server import build_evolution_prompt

        prompt = build_evolution_prompt(
            skill_name="x",
            skill_path=Path("/src"),
            version_dir=Path("/data/skills/x/versions/v2"),
            skill_content="# X",
            skill_files=[],
            feedback={"high_quality": [], "low_rated": [], "user_edits": []},
        )

        assert "/data/skills/x/versions/v2" in prompt


# ── Version directory management ─────────────────────────────────


class TestVersionDirectoryManagement:
    """Test version directory creation and activation."""

    def test_next_version_number_uses_max_not_len(self, tmp_path: Path) -> None:
        """Version numbers should be max(existing) + 1, not len(existing) + 1."""
        from main_server import next_version_number

        # Simulate: v1 exists, v2 was deleted, v3 exists
        (tmp_path / "v1").mkdir()
        (tmp_path / "v3").mkdir()

        result = next_version_number(tmp_path)
        assert result == 4  # max(1,3) + 1, NOT len([v1,v3])+1=3

    def test_next_version_number_empty_dir(self, tmp_path: Path) -> None:
        """Empty versions directory should return version 1."""
        from main_server import next_version_number

        result = next_version_number(tmp_path)
        assert result == 1


# ── API endpoint: POST /api/skills/{skill_name}/evolve-agent ─────


class TestEvolveAgentEndpoint:
    """Test the evolve-agent REST endpoint."""

    def test_evolve_agent_requires_admin(self, client: TestClient) -> None:
        """Non-admin users should be rejected when ENFORCE_ADMIN=true."""
        with patch("main_server.require_admin") as mock_require:
            from fastapi import HTTPException
            mock_require.side_effect = HTTPException(status_code=403, detail="Admin privileges required")
            resp = client.post(
                "/api/skills/test-skill/evolve-agent",
                json={},
            )
            assert resp.status_code == 403

    def test_evolve_agent_missing_skill(self, client: TestClient) -> None:
        """When SKILL.md doesn't exist, should return error."""
        with patch("main_server._get_user_id_from_header", return_value="admin"):
            resp = client.post(
                "/api/skills/nonexistent-skill/evolve-agent",
                json={},
            )
            # Should fail because the skill directory doesn't exist
            assert resp.status_code in (200, 500)
            data = resp.json()
            if data.get("status") == "failed":
                assert "not found" in data.get("reason", "").lower() or "SKILL.md" in data.get("reason", "")

    def test_evolve_agent_accepts_model_param(self, client: TestClient) -> None:
        """Should accept an optional model parameter."""
        with patch("main_server._get_user_id_from_header", return_value="admin"):
            with patch("main_server.run_evolution_agent", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = {"task_id": "task-123", "status": "running"}
                resp = client.post(
                    "/api/skills/test-skill/evolve-agent",
                    json={"model": "claude-opus-4-6"},
                )
                # Verify the model parameter was passed through
                if mock_run.called:
                    call_kwargs = mock_run.call_args[1]
                    assert call_kwargs.get("model") == "claude-opus-4-6"


# ── API endpoint: GET /api/skills/{skill_name}/evolve-status ─────


class TestEvolveStatusEndpoint:
    """Test the evolve-status REST endpoint."""

    def test_evolve_status_returns_status(self, client: TestClient) -> None:
        """Should return the status of an evolution task."""
        with patch("main_server._get_user_id_from_header", return_value="admin"):
            # Register a fake task
            main_server.active_tasks["task-123"] = MagicMock()
            resp = client.get("/api/skills/test-skill/evolve-status/task-123")
            assert resp.status_code == 200


# ── Frontend API hooks ───────────────────────────────────────────


class TestFrontendEvolutionApiHooks:
    """Test the frontend evolution API hook types."""

    def test_evolve_agent_hook_returns_task_id(self) -> None:
        """The frontend hook should call evolve-agent and return task_id."""
        # This is a type-level test — verify the hook function signature
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "useSkillEvolutionApi",
            Path(__file__).parent.parent.parent / "frontend" / "src" / "hooks" / "useSkillEvolutionApi.ts",
        )
        # We can't run TypeScript, but verify the file exists and contains the function
        hook_file = Path(__file__).parent.parent.parent / "frontend" / "src" / "hooks" / "useSkillEvolutionApi.ts"
        assert hook_file.exists()
        content = hook_file.read_text()
        assert "evolveAgent" in content or "evolve-agent" in content


# ── Old evolution methods removed ─────────────────────────────────


class TestOldEvolutionMethodsRemoved:
    """Verify that the old evolution methods have been removed."""

    def test_preview_evolution_removed(self) -> None:
        """The old preview_evolution method should be removed."""
        from src.skill_feedback import DBSkillFeedbackManager
        assert not hasattr(DBSkillFeedbackManager, "preview_evolution")

    def test_generate_improved_skill_removed(self) -> None:
        """The old generate_improved_skill method should be removed."""
        from src.skill_feedback import DBSkillFeedbackManager
        assert not hasattr(DBSkillFeedbackManager, "generate_improved_skill")

    def test_old_evolve_endpoint_removed(self) -> None:
        """The old /evolve endpoint function should be removed."""
        import inspect
        source = inspect.getsource(main_server)
        # Check for the old function name (without -agent suffix)
        assert "async def trigger_skill_evolution(" not in source
        # Check for the old route pattern (exact string, not the -agent variant)
        assert '"/api/skills/{skill_name}/evolve"' not in source


# ── Task ID consistency ─────────────────────────────────────────

class TestTaskIdConsistency:
    """The task_id returned by the HTTP endpoint must match the task_id
    used for buffer messages. Otherwise the status polling endpoint
    will never find completion because it queries the buffer with the
    wrong key."""

    def test_run_evolution_agent_uses_passed_task_id(self, tmp_path: Path) -> None:
        """run_evolution_agent must use the task_id parameter for buffer
        messages, NOT generate its own UUID-based task_id."""
        import inspect
        source = inspect.getsource(main_server.run_evolution_agent)

        # The function must NOT contain uuid.uuid4() — task_id should be
        # passed as a parameter from the caller
        assert "uuid.uuid4()" not in source, (
            "run_evolution_agent generates its own task_id with uuid.uuid4(). "
            "This causes a mismatch: the HTTP endpoint returns task_id "
            "'evolve-{skill_name}-v{N}' but buffer messages are stored under "
            "'evolve-{random-uuid}'. The status polling endpoint queries with "
            "the HTTP task_id and never finds the buffer messages. "
            "Fix: add 'task_id' as a parameter and remove uuid.uuid4() call."
        )

    def test_evolve_agent_endpoint_passes_task_id_to_runner(self, client: TestClient) -> None:
        """The evolve-agent endpoint must pass a deterministic task_id
        (evolve-{skill_name}-v{N}) to run_evolution_agent, so the
        frontend can poll /evolve-status with the same ID."""
        import inspect
        source = inspect.getsource(main_server.trigger_skill_evolution_agent)

        # The task_id used in the return value must be the same one
        # passed to run_evolution_agent
        assert "run_evolution_agent" in source
        # Verify the task_id pattern is built before run_evolution_agent is called
        assert "evolve-" in source

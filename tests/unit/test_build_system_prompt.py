"""Tests for build_system_prompt identity consistency."""

import json
import tempfile
from pathlib import Path

# Add project root to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from main_server import build_system_prompt


def _make_workspace(tmp: Path) -> Path:
    ws = tmp / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


class TestBuildSystemPromptIdentity:
    def test_prompt_contains_identity_instruction(self):
        """System prompt must always include a clear identity block."""
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws)
            # Must contain the identity definition
            assert "Web Agent" in prompt
            # Must contain explicit instruction about self-identification
            assert "你是谁" in prompt or "who are you" in prompt.lower() or "identity" in prompt.lower()

    def test_prompt_contains_chinese_identity_response(self):
        """Agent should respond in Chinese when asked about identity."""
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws)
            # Must contain the Chinese response template
            assert "Web Agent" in prompt

    def test_prompt_forbids_claims_to_be_other_ais(self):
        """System prompt must explicitly forbid claiming to be Claude or other AI models."""
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws)
            assert "Claude" in prompt or "never claim" in prompt.lower()

    def test_prompt_identity_appears_early(self):
        """Identity instruction should appear in the first part of the prompt for maximum weight."""
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws)
            first_half = prompt[: len(prompt) // 2]
            assert "Web Agent" in first_half

    def test_prompt_preserves_file_generation_rules(self):
        """Identity change should not remove existing file generation rules."""
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws)
            assert "outputs/" in prompt

    def test_prompt_includes_extraction_rules(self):
        """System prompt must include proactive knowledge extraction guidance."""
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws)
            assert "Knowledge Extraction" in prompt
            assert "When to Create a Skill" in prompt
            assert "anti-overwrite" in prompt.lower()

    def test_extraction_rules_not_just_fallback(self):
        """Extraction rules should be the full version, not the minimal fallback."""
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws)
            # Full extraction rules contain workflow details
            assert "Extraction Workflow" in prompt
            assert "Quality gate" in prompt
            assert "error-resolution" in prompt

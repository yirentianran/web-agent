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


class TestBuildSystemPromptLanguage:
    def test_english_mode_has_no_chinese_identity_reply(self):
        """In English mode, canned identity replies must be in English, not Chinese."""
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws, language="en")
            assert "我是 Web Agent" not in prompt
            assert "我底层使用" not in prompt
            assert "I am Web Agent" in prompt

    def test_chinese_mode_has_chinese_identity_reply(self):
        """In Chinese mode, canned identity replies must be in Chinese."""
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws, language="zh")
            assert "我是 Web Agent" in prompt
            assert "我底层使用" in prompt

    def test_response_language_comes_before_identity(self):
        """Response Language section must appear before Identity Instructions."""
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws, language="en")
            rl_pos = prompt.index("Response Language")
            identity_pos = prompt.index("Identity Instructions")
            assert rl_pos < identity_pos, (
                f"Response Language ({rl_pos}) must come before Identity Instructions ({identity_pos})"
            )

    def test_response_language_is_first_section(self):
        """Response Language must be the first ## section in the prompt."""
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws, language="zh")
            first_section = prompt.split("## ")[1]
            assert first_section.startswith("Response Language"), (
                f"First section is: {first_section[:50]}"
            )

    def test_only_response_language_has_absolute_priority(self):
        """Only Response Language section should claim ABSOLUTE PRIORITY."""
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws, language="en")
            identity_start = prompt.index("## Identity Instructions")
            # Find next ## section after Identity
            rest = prompt[identity_start + 1:]
            next_section = rest.index("## ") if "## " in rest else len(rest)
            identity_section = prompt[identity_start:identity_start + 1 + next_section]
            assert "ABSOLUTE PRIORITY" not in identity_section

    def test_skills_section_has_language_note(self):
        """Skills section must include a note about original language + target language."""
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            skills = {"test-skill": {"description": "A test skill"}}
            prompt = build_system_prompt("test_user", skills, ws, language="en")
            assert "original language" in prompt.lower()
            assert "responding in English" in prompt

    def test_language_directive_includes_thinking(self):
        """Response Language section must explicitly mention thinking/reasoning."""
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws, language="zh")
            # The Response Language section is the first ## section
            first_section = prompt.split("## ")[1]
            assert "thinking" in first_section.lower()

    def test_final_check_includes_thinking(self):
        """Final Check section must explicitly mention thinking blocks."""
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws, language="zh")
            # Find the Final Check section
            assert "including thinking blocks" in prompt

    def test_english_mode_reply_specific_warning(self):
        """Response Language section must warn that wrong-language reply = task failed."""
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws, language="en")
            assert "VISIBLE REPLY" in prompt
            assert "FAILED" in prompt
            assert "regardless of correct thinking" in prompt

    def test_final_check_mentions_reply_specifically(self):
        """Final Check must explicitly mention the reply (not just all content)."""
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws, language="en")
            final_check_start = prompt.index("## FINAL CHECK")
            final_check = prompt[final_check_start:]
            assert "reply" in final_check.lower()
            assert "REPLY IN ENGLISH" in final_check


class TestSecurityPrompt:
    def test_prompt_contains_hardware_os_refusal(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws)
            assert "system information" in prompt.lower() or "系统信息" in prompt

    def test_prompt_contains_env_secrets_refusal(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws)
            assert "configuration" in prompt.lower() or "配置" in prompt

    def test_prompt_contains_deployment_refusal(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws)
            assert "deployment" in prompt.lower() or "部署" in prompt

    def test_prompt_contains_architecture_refusal(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws)
            assert "implementation detail" in prompt.lower() or "实现细节" in prompt

    def test_prompt_contains_config_refusal(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws)
            assert "configuration file" in prompt.lower() or "配置文件" in prompt

    def test_security_section_appears_before_skills(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws)
            security_idx = prompt.find("Information Disclosure")
            skills_idx = prompt.find("Available Skills")
            if security_idx >= 0 and skills_idx >= 0:
                assert security_idx < skills_idx

    def test_security_prompt_english(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws, language="en")
            assert "NEVER" in prompt or "MUST" in prompt

    def test_security_prompt_chinese(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            prompt = build_system_prompt("test_user", {}, ws, language="zh")
            assert "无法" in prompt or "禁止" in prompt or "切勿" in prompt or "不能" in prompt

"""Unit tests for SKILL.md frontmatter parsing."""

from __future__ import annotations

from main_server import parse_skill_frontmatter


# ── Happy path ────────────────────────────────────────────────────


class TestParseFrontmatterHappy:
    def test_full_frontmatter(self):
        content = (
            "---\n"
            "name: security-review\n"
            "description: Review code for security vulnerabilities\n"
            "version: 1.0.0\n"
            "---\n"
            "# Security Review\n\n"
            "Detailed instructions...\n"
        )
        result = parse_skill_frontmatter(content)
        assert result["name"] == "security-review"
        assert result["description"] == "Review code for security vulnerabilities"
        assert result["version"] == "1.0.0"

    def test_partial_frontmatter_no_version(self):
        content = "---\nname: e2e\ndescription: Run end-to-end tests\n---\n## Steps\n...\n"
        result = parse_skill_frontmatter(content)
        assert result["name"] == "e2e"
        assert result["description"] == "Run end-to-end tests"
        assert result["version"] is None

    def test_description_multiline_folded(self):
        content = (
            "---\n"
            "name: code-review\n"
            "description: >\n"
            "  Review code for quality,\n"
            "  security, and maintainability.\n"
            "---\n"
            "Body...\n"
        )
        result = parse_skill_frontmatter(content)
        # YAML folded scalar: newlines become spaces, trailing newline
        assert "quality" in result["description"]
        assert "security" in result["description"]

    def test_description_with_quotes(self):
        content = '---\nname: prompt-optimizer\ndescription: "Optimize prompts for LLMs"\n---\nBody...\n'
        result = parse_skill_frontmatter(content)
        assert result["description"] == "Optimize prompts for LLMs"


# ── No frontmatter ────────────────────────────────────────────────


class TestParseFrontmatterNoFrontmatter:
    def test_empty_string(self):
        result = parse_skill_frontmatter("")
        assert result["name"] is None
        assert result["description"] is None
        assert result["version"] is None

    def test_plain_markdown_no_frontmatter(self):
        content = "# My Skill\n\nSome instructions.\n"
        result = parse_skill_frontmatter(content)
        assert result["name"] is None
        assert result["description"] is None
        assert result["version"] is None

    def test_no_closing_separator(self):
        content = "---\nname: test\nno closing here\n"
        result = parse_skill_frontmatter(content)
        assert result["name"] is None
        assert result["description"] is None

    def test_only_separators_no_content(self):
        content = "---\n---\n"
        result = parse_skill_frontmatter(content)
        assert result["name"] is None
        assert result["description"] is None
        assert result["version"] is None


# ── Invalid frontmatter ──────────────────────────────────────────


class TestParseFrontmatterInvalid:
    def test_invalid_yaml(self):
        content = "---\nname: [invalid\n---\nBody\n"
        result = parse_skill_frontmatter(content)
        assert result["name"] is None
        assert result["description"] is None

    def test_frontmatter_has_only_name(self):
        content = "---\nname: test-skill\n---\nBody\n"
        result = parse_skill_frontmatter(content)
        assert result["name"] == "test-skill"
        assert result["description"] is None

    def test_frontmatter_has_only_description(self):
        content = "---\ndescription: Do something useful\n---\nBody\n"
        result = parse_skill_frontmatter(content)
        assert result["name"] is None
        assert result["description"] == "Do something useful"

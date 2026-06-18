"""Tests for SecurityEnforcer shared pre-execution security checks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.security.enforcer import SecurityEnforcer


@pytest.fixture
def enforcer(tmp_path: Path) -> SecurityEnforcer:
    return SecurityEnforcer(
        user_id="test_user",
        workspace=tmp_path / "workspace",
        user_dir=tmp_path / "user_data",
    )


class TestCheckBash:
    def test_allows_safe_command(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_bash("ls -la")
        assert allowed is True
        assert reason == ""

    def test_denies_empty_command(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_bash("")
        assert allowed is False
        assert "Empty" in reason

    def test_denies_env_command(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_bash("env")
        assert allowed is False

    def test_denies_curl(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_bash("curl http://example.com")
        assert allowed is False

    def test_allows_git_diff(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_bash("git diff HEAD~1")
        assert allowed is True


class TestCheckWritePath:
    def test_allows_normal_path(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_write_path("outputs/report.txt")
        assert allowed is True

    def test_denies_empty_path(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_write_path("")
        assert allowed is False

    def test_denies_null_path(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_write_path("null")
        assert allowed is False

    def test_denies_undefined_path(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_write_path("undefined")
        assert allowed is False

    def test_denies_env_file(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_write_path(".env")
        assert allowed is False

    def test_denies_dockerfile(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_write_path("Dockerfile")
        assert allowed is False


class TestCheckReadPath:
    def test_allows_normal_path(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_read_path("outputs/report.txt")
        assert allowed is True

    def test_denies_empty_path(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_read_path("")
        assert allowed is False

    def test_denies_env_file(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_read_path(".env.local")
        assert allowed is False

    def test_allows_py_file(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_read_path("src/main.py")
        assert allowed is True


class TestCheckReadSize:
    def test_allows_small_file(self, enforcer: SecurityEnforcer, tmp_path: Path) -> None:
        f = tmp_path / "small.txt"
        f.write_text("hello")
        allowed, reason = enforcer.check_read_size(str(f), max_bytes=1024 * 1024)
        assert allowed is True

    def test_denies_oversized_file(self, enforcer: SecurityEnforcer, tmp_path: Path) -> None:
        f = tmp_path / "large.txt"
        f.write_text("x" * 1000)
        allowed, reason = enforcer.check_read_size(str(f), max_bytes=10)
        assert allowed is False
        assert "MB" in reason

    def test_allows_when_max_bytes_zero(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_read_size("/nonexistent/path", max_bytes=0)
        assert allowed is True

    def test_allows_nonexistent_file(self, enforcer: SecurityEnforcer) -> None:
        allowed, reason = enforcer.check_read_size("/nonexistent/path.txt", max_bytes=1024)
        assert allowed is True

"""Tests for A/B testing: hash assignment, result tracking, winner detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ab_testing import MIN_SAMPLES_PER_VERSION, SkillABTest


class TestHashAssignment:
    def test_deterministic(self, tmp_path: Path) -> None:
        test = SkillABTest("test-skill", "v1", "v2", data_root=tmp_path)
        # Same user always gets same version
        assert test.assign_version("alice") == test.assign_version("alice")

    def test_roughly_balanced(self, tmp_path: Path) -> None:
        test = SkillABTest("test-skill", "v1", "v2", data_root=tmp_path)
        a_count = sum(
            1 for i in range(100)
            if test.assign_version(f"user_{i}") == "v1"
        )
        # 50/50 split: expect 40-60 out of 100 in version A
        assert 40 <= a_count <= 60

    def test_returns_valid_version(self, tmp_path: Path) -> None:
        test = SkillABTest("test-skill", "v1", "v2", data_root=tmp_path)
        for i in range(10):
            v = test.assign_version(f"user_{i}")
            assert v in ("v1", "v2")


class TestRecordResult:
    def test_record_success(self, tmp_path: Path) -> None:
        test = SkillABTest("test-skill", "v1", "v2", data_root=tmp_path)
        entry = test.record_result("alice", "a", 5)
        assert entry["version"] == "a"
        assert entry["rating"] == 5
        assert entry["user_id"] == "alice"

    def test_invalid_version_raises(self, tmp_path: Path) -> None:
        test = SkillABTest("test-skill", "v1", "v2", data_root=tmp_path)
        with pytest.raises(ValueError, match="must be"):
            test.record_result("alice", "c", 3)

    def test_invalid_rating_raises(self, tmp_path: Path) -> None:
        test = SkillABTest("test-skill", "v1", "v2", data_root=tmp_path)
        with pytest.raises(ValueError, match="Rating must be between"):
            test.record_result("alice", "a", 0)
        with pytest.raises(ValueError, match="Rating must be between"):
            test.record_result("alice", "a", 6)


class TestIsWinner:
    def test_no_results(self, tmp_path: Path) -> None:
        test = SkillABTest("test-skill", "v1", "v2", data_root=tmp_path)
        result = test.is_winner()
        assert result.version_a_count == 0
        assert result.version_b_count == 0
        assert result.winner is None
        assert not result.is_decisive

    def test_insufficient_samples(self, tmp_path: Path) -> None:
        test = SkillABTest("test-skill", "v1", "v2", data_root=tmp_path)
        # Only 3 samples per version (need 5 minimum)
        for i in range(3):
            test.record_result(f"user_a_{i}", "a", 5)
            test.record_result(f"user_b_{i}", "b", 2)
        result = test.is_winner()
        assert result.winner is None  # not enough samples

    def test_clear_winner(self, tmp_path: Path) -> None:
        test = SkillABTest("test-skill", "v1", "v2", data_root=tmp_path)
        for i in range(10):
            test.record_result(f"user_a_{i}", "a", 2)
            test.record_result(f"user_b_{i}", "b", 5)
        result = test.is_winner()
        assert result.winner == "b"
        assert result.is_decisive

    def test_no_winner_close_ratings(self, tmp_path: Path) -> None:
        test = SkillABTest("test-skill", "v1", "v2", data_root=tmp_path)
        for i in range(10):
            test.record_result(f"user_a_{i}", "a", 4)
            test.record_result(f"user_b_{i}", "b", 4)
        result = test.is_winner()
        assert result.winner is None
        assert not result.is_decisive

    def test_get_results_format(self, tmp_path: Path) -> None:
        test = SkillABTest("test-skill", "v1", "v2", data_root=tmp_path)
        test.record_result("alice", "a", 4)
        test.record_result("bob", "b", 5)

        results = test.get_results()
        assert results["skill_name"] == "test-skill"
        assert results["version_a"] == "v1"
        assert results["version_b"] == "v2"
        assert results["version_a_count"] == 1
        assert results["version_b_count"] == 1
        assert "winner" in results
        assert "is_decisive" in results

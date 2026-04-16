"""Tests for skill evolution: feedback collection, should_evolve, version generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.skill_evolution import (
    EvolutionCandidate,
    FeedbackStats,
    SkillEvolutionManager,
)


class TestCollectFeedback:
    def test_submit_feedback(self, tmp_path: Path) -> None:
        mgr = SkillEvolutionManager(tmp_path)
        entry = mgr.collect_feedback("test-skill", rating=5, user_id="alice")
        assert entry["rating"] == 5
        assert entry["skill_name"] == "test-skill"
        assert entry["user_id"] == "alice"

    def test_invalid_rating_raises(self, tmp_path: Path) -> None:
        mgr = SkillEvolutionManager(tmp_path)
        with pytest.raises(ValueError, match="Rating must be between"):
            mgr.collect_feedback("test-skill", rating=0)
        with pytest.raises(ValueError, match="Rating must be between"):
            mgr.collect_feedback("test-skill", rating=6)

    def test_comment_truncated(self, tmp_path: Path) -> None:
        mgr = SkillEvolutionManager(tmp_path)
        entry = mgr.collect_feedback("test-skill", rating=3, comment="x" * 1000)
        assert len(entry["comment"]) == 500


class TestGetFeedbackStats:
    def test_empty_stats(self, tmp_path: Path) -> None:
        mgr = SkillEvolutionManager(tmp_path)
        stats = mgr.get_feedback_stats("nonexistent")
        assert stats.count == 0
        assert stats.average_rating == 0.0
        assert stats.high_quality_count == 0

    def test_stats_with_feedback(self, tmp_path: Path) -> None:
        mgr = SkillEvolutionManager(tmp_path)
        mgr.collect_feedback("test-skill", rating=5)
        mgr.collect_feedback("test-skill", rating=3)
        mgr.collect_feedback("test-skill", rating=4)

        stats = mgr.get_feedback_stats("test-skill")
        assert stats.count == 3
        assert stats.average_rating == 4.0
        assert stats.high_quality_count == 2  # ratings 5 and 4
        assert "5" in stats.rating_distribution

    def test_versions_tracked(self, tmp_path: Path) -> None:
        mgr = SkillEvolutionManager(tmp_path)
        mgr.collect_feedback("test-skill", rating=4, version="v1")
        mgr.collect_feedback("test-skill", rating=3, version="v2")

        stats = mgr.get_feedback_stats("test-skill")
        assert "v1" in stats.versions
        assert "v2" in stats.versions


class TestShouldEvolve:
    def test_not_enough_feedback(self, tmp_path: Path) -> None:
        mgr = SkillEvolutionManager(tmp_path)
        for i in range(5):
            mgr.collect_feedback("test-skill", rating=2)
        assert not mgr.should_evolve("test-skill")

    def test_low_ratings_trigger_evolve(self, tmp_path: Path) -> None:
        mgr = SkillEvolutionManager(tmp_path)
        for i in range(12):
            mgr.collect_feedback("test-skill", rating=2)
        assert mgr.should_evolve("test-skill")

    def test_high_ratings_no_evolve(self, tmp_path: Path) -> None:
        mgr = SkillEvolutionManager(tmp_path)
        for i in range(15):
            mgr.collect_feedback("test-skill", rating=5)
        assert not mgr.should_evolve("test-skill")

    def test_barely_below_threshold(self, tmp_path: Path) -> None:
        mgr = SkillEvolutionManager(tmp_path)
        for i in range(5):
            mgr.collect_feedback("test-skill", rating=4)
        for i in range(5):
            mgr.collect_feedback("test-skill", rating=5)
        assert not mgr.should_evolve("test-skill")


class TestGetEvolutionCandidates:
    def test_returns_sorted_by_rating(self, tmp_path: Path) -> None:
        mgr = SkillEvolutionManager(tmp_path)
        # Bad skill: avg 2.0
        for i in range(12):
            mgr.collect_feedback("bad-skill", rating=2)
        # Mediocre skill: avg 3.5
        for i in range(12):
            mgr.collect_feedback("mid-skill", rating=3)
            mgr.collect_feedback("mid-skill", rating=4)

        candidates = mgr.get_evolution_candidates()
        names = [c.skill_name for c in candidates]
        # bad-skill (avg 2.0) should come before mid-skill (avg 3.5)
        assert "bad-skill" in names
        assert "mid-skill" in names
        assert names.index("bad-skill") < names.index("mid-skill")

    def test_no_candidates(self, tmp_path: Path) -> None:
        mgr = SkillEvolutionManager(tmp_path)
        candidates = mgr.get_evolution_candidates()
        assert candidates == []

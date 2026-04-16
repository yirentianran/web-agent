"""Tests for skill feedback collection and analytics."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.skill_feedback import SkillFeedbackManager


class TestSkillFeedbackManager:
    def test_submit_feedback(self, tmp_path: Path) -> None:
        mgr = SkillFeedbackManager(data_root=tmp_path)
        entry = mgr.submit_feedback("audit-pdf", user_id="alice", rating=4, comment="Good")
        assert entry["rating"] == 4
        assert entry["skill_name"] == "audit-pdf"
        assert entry["user_id"] == "alice"

    def test_invalid_rating_raises(self, tmp_path: Path) -> None:
        mgr = SkillFeedbackManager(data_root=tmp_path)
        with pytest.raises(ValueError, match="Rating must be between"):
            mgr.submit_feedback("test", user_id="alice", rating=0)
        with pytest.raises(ValueError, match="Rating must be between"):
            mgr.submit_feedback("test", user_id="alice", rating=6)

    def test_get_analytics_empty(self, tmp_path: Path) -> None:
        mgr = SkillFeedbackManager(data_root=tmp_path)
        analytics = mgr.get_analytics("nonexistent")
        assert analytics["total_feedbacks"] == 0
        assert analytics["average_rating"] == 0

    def test_get_analytics_with_feedback(self, tmp_path: Path) -> None:
        mgr = SkillFeedbackManager(data_root=tmp_path)
        mgr.submit_feedback("audit-pdf", user_id="alice", rating=5)
        mgr.submit_feedback("audit-pdf", user_id="bob", rating=3)
        mgr.submit_feedback("audit-pdf", user_id="carol", rating=4, comment="Decent")

        analytics = mgr.get_analytics("audit-pdf")
        assert analytics["total_feedbacks"] == 3
        assert analytics["average_rating"] == 4.0
        assert "5" in analytics["rating_distribution"]

    def test_get_all_analytics(self, tmp_path: Path) -> None:
        mgr = SkillFeedbackManager(data_root=tmp_path)
        mgr.submit_feedback("skill-a", user_id="alice", rating=4)
        mgr.submit_feedback("skill-b", user_id="bob", rating=2)

        all_analytics = mgr.get_all_analytics()
        assert "skill-a" in all_analytics
        assert "skill-b" in all_analytics
        assert all_analytics["skill-a"]["average_rating"] == 4.0

    def test_suggest_improvements_low_ratings(self, tmp_path: Path) -> None:
        mgr = SkillFeedbackManager(data_root=tmp_path)
        for i in range(5):
            mgr.submit_feedback(
                "bad-skill",
                user_id=f"user{i}",
                rating=1,
                comment="This is wrong and outdated",
            )

        suggestions = mgr.suggest_improvements("bad-skill")
        assert len(suggestions) > 0
        assert any("50%+" in s for s in suggestions)

    def test_suggest_improvements_no_feedback(self, tmp_path: Path) -> None:
        mgr = SkillFeedbackManager(data_root=tmp_path)
        assert mgr.suggest_improvements("no-feedback") == []

    def test_comment_truncated(self, tmp_path: Path) -> None:
        mgr = SkillFeedbackManager(data_root=tmp_path)
        long_comment = "x" * 1000
        entry = mgr.submit_feedback("test", user_id="alice", rating=3, comment=long_comment)
        assert len(entry["comment"]) <= 500

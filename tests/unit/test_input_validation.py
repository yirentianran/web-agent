"""Tests for Pydantic input validation on API request models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

import main_server


class TestTitleUpdateValidation:
    def test_title_exceeds_max_length(self) -> None:
        with pytest.raises(ValidationError):
            main_server.TitleUpdate(title="x" * 501)

    def test_empty_title_rejected(self) -> None:
        with pytest.raises(ValidationError):
            main_server.TitleUpdate(title="")

    def test_valid_title_accepted(self) -> None:
        model = main_server.TitleUpdate(title="My session")
        assert model.title == "My session"


class TestTaskCreateRequestValidation:
    def test_subject_exceeds_max_length(self) -> None:
        with pytest.raises(ValidationError):
            main_server.TaskCreateRequest(subject="x" * 201)

    def test_empty_subject_rejected(self) -> None:
        with pytest.raises(ValidationError):
            main_server.TaskCreateRequest(subject="")

    def test_valid_subject_accepted(self) -> None:
        model = main_server.TaskCreateRequest(subject="Fix bug")
        assert model.subject == "Fix bug"


class TestSkillFeedbackRequestValidation:
    def test_comment_exceeds_max_length(self) -> None:
        with pytest.raises(ValidationError):
            main_server.SkillFeedbackRequest(rating=5, comment="x" * 5001)

    def test_rating_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            main_server.SkillFeedbackRequest(rating=0)
        with pytest.raises(ValidationError):
            main_server.SkillFeedbackRequest(rating=6)

    def test_valid_feedback_accepted(self) -> None:
        model = main_server.SkillFeedbackRequest(rating=4, comment="Great!")
        assert model.rating == 4
        assert model.comment == "Great!"


class TestTokenRequestValidation:
    def test_user_id_exceeds_max_length(self) -> None:
        with pytest.raises(ValidationError):
            main_server.TokenRequest(user_id="x" * 65, password="test")

    def test_password_exceeds_max_length(self) -> None:
        with pytest.raises(ValidationError):
            main_server.TokenRequest(user_id="alice", password="x" * 129)

    def test_valid_token_request_accepted(self) -> None:
        model = main_server.TokenRequest(user_id="alice", password="secret")
        assert model.user_id == "alice"

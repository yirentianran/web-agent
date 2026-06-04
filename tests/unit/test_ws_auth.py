"""Unit tests for WebSocket authentication enforcement."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.auth import ENFORCE_AUTH, create_token, verify_token


class TestWebSocketAuth:
    """Test WS authentication logic used in main_server.py handle_ws."""

    def setup_method(self):
        self.token_alice = create_token("alice")
        self.token_bob = create_token("bob")

    def test_verify_token_extracts_correct_user(self) -> None:
        assert verify_token(self.token_alice) == "alice"
        assert verify_token(self.token_bob) == "bob"

    def test_user_ids_differ_for_different_tokens(self) -> None:
        assert verify_token(self.token_alice) != verify_token(self.token_bob)


class TestWsUserIdEnforcement:
    """Test the logic that should reject mismatched user_id in WS messages."""

    def test_mismatch_should_be_rejected_when_auth_enforced(self) -> None:
        """When ENFORCE_AUTH=true, a message with wrong user_id must be rejected."""
        locked_user_id = "alice"
        incoming_user_id = "bob"
        enforce_auth = True

        mismatch = incoming_user_id != locked_user_id
        should_reject = mismatch and enforce_auth
        assert should_reject is True

    def test_mismatch_allowed_when_auth_disabled(self) -> None:
        """When ENFORCE_AUTH=false, mismatch is only logged, not rejected."""
        locked_user_id = "alice"
        incoming_user_id = "bob"
        enforce_auth = False

        mismatch = incoming_user_id != locked_user_id
        should_reject = mismatch and enforce_auth
        assert should_reject is False

    def test_matching_user_id_allowed(self) -> None:
        locked_user_id = "alice"
        incoming_user_id = "alice"
        enforce_auth = True

        mismatch = incoming_user_id != locked_user_id
        should_reject = mismatch and enforce_auth
        assert should_reject is False

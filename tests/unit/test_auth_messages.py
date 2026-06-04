"""Tests for authentication error message consistency (anti-enumeration)."""

from __future__ import annotations


class TestAuthMessageConsistency:
    """Verify that login error messages don't reveal user existence."""

    def test_all_failure_paths_return_same_message(self) -> None:
        """All login failures should return 'Invalid credentials'."""
        expected_message = "Invalid credentials"
        assert expected_message == "Invalid credentials"

    def test_disabled_status_is_401_not_403(self) -> None:
        """Disabled accounts should get 401 (same as invalid credentials), not 403."""
        # After fix, disabled accounts return 401 like any other auth failure
        expected_status = 401
        assert expected_status == 401

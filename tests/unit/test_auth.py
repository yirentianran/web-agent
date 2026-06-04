"""Unit tests for JWT authentication module."""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException, Request

from src.auth import (
    ACCESS_TOKEN_COOKIE,
    ALGORITHM,
    CSRF_HEADER,
    CSRF_TOKEN_COOKIE,
    JWT_SECRET,
    SAFE_METHODS,
    create_csrf_token,
    create_token,
    get_current_user,
    require_user_match,
    verify_csrf,
    verify_token,
)


# ── create_csrf_token ─────────────────────────────────────────────


class TestCreateCsrfToken:
    def test_token_is_hex_string(self) -> None:
        token = create_csrf_token()
        assert len(token) == 64  # 32 bytes hex = 64 chars
        assert all(c in "0123456789abcdef" for c in token)

    def test_tokens_are_unique(self) -> None:
        tokens = {create_csrf_token() for _ in range(100)}
        assert len(tokens) == 100


# ── verify_csrf ───────────────────────────────────────────────────


class TestVerifyCsrf:
    def _make_request(self, method: str, cookie_value: str = "", header_value: str = "") -> MagicMock:
        """Helper to build a mock Request with given CSRF state."""
        req = MagicMock(spec=Request)
        req.method = method
        req.cookies = {CSRF_TOKEN_COOKIE: cookie_value}
        req.headers = {CSRF_HEADER: header_value}
        return req

    @patch("src.auth.ENFORCE_AUTH", True)
    def test_safe_methods_are_skipped(self) -> None:
        for method in ("GET", "HEAD", "OPTIONS"):
            req = self._make_request(method)
            verify_csrf(req)  # Should not raise

    @patch("src.auth.ENFORCE_AUTH", True)
    def test_post_without_csrf_header_raises_403(self) -> None:
        req = self._make_request("POST", cookie_value="abc")
        with pytest.raises(HTTPException) as exc:
            verify_csrf(req)
        assert exc.value.status_code == 403
        assert "CSRF" in exc.value.detail

    @patch("src.auth.ENFORCE_AUTH", True)
    def test_post_without_csrf_cookie_raises_403(self) -> None:
        req = self._make_request("POST", header_value="abc")
        with pytest.raises(HTTPException) as exc:
            verify_csrf(req)
        assert exc.value.status_code == 403

    @patch("src.auth.ENFORCE_AUTH", True)
    def test_post_with_mismatched_csrf_raises_403(self) -> None:
        req = self._make_request("POST", cookie_value="token_a", header_value="token_b")
        with pytest.raises(HTTPException) as exc:
            verify_csrf(req)
        assert exc.value.status_code == 403

    @patch("src.auth.ENFORCE_AUTH", True)
    def test_post_with_matching_csrf_passes(self) -> None:
        token = create_csrf_token()
        req = self._make_request("POST", cookie_value=token, header_value=token)
        verify_csrf(req)  # Should not raise

    @patch("src.auth.ENFORCE_AUTH", True)
    def test_delete_with_matching_csrf_passes(self) -> None:
        token = create_csrf_token()
        req = self._make_request("DELETE", cookie_value=token, header_value=token)
        verify_csrf(req)  # Should not raise

    @patch("src.auth.ENFORCE_AUTH", False)
    def test_skipped_when_auth_disabled(self) -> None:
        req = self._make_request("POST")  # No cookie, no header
        verify_csrf(req)  # Should not raise when auth disabled


# ── create_token ──────────────────────────────────────────────────


class TestCreateToken:
    def test_token_is_valid_jwt(self) -> None:
        token = create_token("alice")
        # Should not raise
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        assert payload["sub"] == "alice"

    def test_token_has_correct_expiry(self) -> None:
        token = create_token("bob", expires_minutes=30)
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        now = datetime.now(timezone.utc)
        # Should expire roughly 30 minutes from now
        assert now < exp < now + timedelta(minutes=31)

    def test_different_users_get_different_tokens(self) -> None:
        t1 = create_token("alice")
        t2 = create_token("bob")
        assert jwt.decode(t1, JWT_SECRET, algorithms=[ALGORITHM])["sub"] == "alice"
        assert jwt.decode(t2, JWT_SECRET, algorithms=[ALGORITHM])["sub"] == "bob"


# ── verify_token ──────────────────────────────────────────────────


class TestVerifyToken:
    def test_valid_token_returns_user_id(self) -> None:
        token = create_token("alice")
        user_id = verify_token(token)
        assert user_id == "alice"

    def test_expired_token_raises_401(self) -> None:
        # Create a token that expired 1 minute ago
        now = datetime.now(timezone.utc)
        expired = jwt.encode(
            {"sub": "alice", "iat": now - timedelta(hours=1), "exp": now - timedelta(minutes=1)},
            JWT_SECRET,
            algorithm=ALGORITHM,
        )
        with pytest.raises(HTTPException) as exc_info:
            verify_token(expired)
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()

    def test_tampered_token_raises_401(self) -> None:
        token = create_token("alice")
        # Tamper: flip a character
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        with pytest.raises(HTTPException) as exc_info:
            verify_token(tampered)
        assert exc_info.value.status_code == 401

    def test_random_string_raises_401(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            verify_token("not-a-jwt-token")
        assert exc_info.value.status_code == 401

    def test_token_missing_sub_raises_401(self) -> None:
        # Craft a token without the "sub" claim
        no_sub = jwt.encode(
            {"iat": datetime.now(timezone.utc), "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            JWT_SECRET,
            algorithm=ALGORITHM,
        )
        with pytest.raises(HTTPException) as exc_info:
            verify_token(no_sub)
        assert exc_info.value.status_code == 401


# ── get_current_user ──────────────────────────────────────────────


class TestGetCurrentUser:
    def test_returns_default_when_auth_disabled(self) -> None:
        """When ENFORCE_AUTH=False, returns 'default' without token."""
        user_id = get_current_user(token=None)
        assert user_id == "default"

    def test_raises_when_no_token_and_auth_enabled(self) -> None:
        """When ENFORCE_AUTH=True and no token, raises 401."""
        with patch("src.auth.ENFORCE_AUTH", True):
            with pytest.raises(HTTPException) as exc_info:
                get_current_user(token=None)
            assert exc_info.value.status_code == 401

    def test_returns_user_with_valid_token(self) -> None:
        """When ENFORCE_AUTH=True and valid token, returns user_id."""
        with patch("src.auth.ENFORCE_AUTH", True):
            token = create_token("alice")
            user_id = get_current_user(token=token)
            assert user_id == "alice"


# ── require_user_match ────────────────────────────────────────────


class TestRequireUserMatch:
    def test_passthrough_when_auth_disabled(self) -> None:
        """Returns path_user_id regardless of current_user."""
        result = require_user_match(path_user_id="bob", current_user="alice")
        assert result == "bob"

    def test_returns_user_when_ids_match(self) -> None:
        """Returns the user_id when they match."""
        with patch("src.auth.ENFORCE_AUTH", True):
            result = require_user_match(path_user_id="alice", current_user="alice")
            assert result == "alice"

    def test_raises_403_when_ids_differ(self) -> None:
        """Raises 403 when current_user differs from path_user_id."""
        with patch("src.auth.ENFORCE_AUTH", True):
            with pytest.raises(HTTPException) as exc_info:
                require_user_match(path_user_id="bob", current_user="alice")
            assert exc_info.value.status_code == 403

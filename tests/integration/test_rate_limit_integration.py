"""Integration tests for API rate limiting.

These tests verify that the global default rate limit (60/minute)
applies to all endpoints via the SlowAPIMiddleware.

Note: Rate limit response headers (Retry-After, X-RateLimit-*) are
not injected by default because the Limiter is configured with
``headers_enabled=False``.  Enabling ``headers_enabled=True`` would
add these headers but also add a small per-request overhead.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


class TestGlobalRateLimiting:
    """Verify that non-auth endpoints are rate-limited."""

    @pytest.fixture(autouse=True)
    def _patch_auth(self):
        """Simulate enforced auth with a valid token for all test methods."""
        with (
            patch("src.auth.ENFORCE_AUTH", True),
            patch("src.auth.verify_token", return_value="alice"),
        ):
            yield

    @pytest.fixture(autouse=True)
    def _reset_limiter(self):
        """Clear the in-memory rate limiter storage between tests.

        The TestClient shares the same app instance across tests, so
        the limiter's in-memory storage accumulates hit counters.
        We reset before each test to ensure isolation.
        """
        from main_server import limiter

        limiter.reset()
        yield
        limiter.reset()

    def _make_authed_post(
        self,
        client: TestClient,
        url: str,
        csrf_token: str = "rate-test-csrf",
        **kwargs,
    ):
        """Make a POST request with valid auth cookies and CSRF header."""
        client.cookies.clear()
        client.cookies.set("access_token", "valid-token")
        client.cookies.set("csrf_token", csrf_token)
        headers = kwargs.pop("headers", {})
        headers["X-CSRF-Token"] = csrf_token
        return client.post(url, headers=headers, **kwargs)

    def test_session_creation_rate_limited(
        self, client: TestClient
    ) -> None:
        """Creating many sessions quickly should eventually hit rate limit.

        The default global limit is 60/minute.  We fire 65 rapid requests
        and expect to see at least one 429 response.
        """
        responses = []
        for _ in range(65):
            resp = self._make_authed_post(client, "/api/users/alice/sessions")
            responses.append(resp.status_code)

        status_set = set(responses)
        assert (
            429 in status_set
        ), f"Expected 429 in responses, got: {sorted(status_set)}"

    def test_file_upload_rate_limited(
        self, client: TestClient
    ) -> None:
        """Uploading files quickly should eventually hit rate limit."""
        responses = []
        for _ in range(65):
            resp = self._make_authed_post(
                client,
                "/api/users/alice/upload",
                files={"file": ("test.txt", b"data", "text/plain")},
            )
            responses.append(resp.status_code)

        status_set = set(responses)
        assert (
            429 in status_set
        ), f"Expected 429 in responses, got: {sorted(status_set)}"

    def test_non_state_changing_requests_also_rate_limited(
        self, client: TestClient
    ) -> None:
        """GET endpoints should also be covered by the global rate limit."""
        client.cookies.clear()
        client.cookies.set("access_token", "valid-token")
        client.cookies.set("csrf_token", "rate-test-csrf")

        responses = []
        for _ in range(65):
            resp = client.get("/api/users/alice/sessions")
            responses.append(resp.status_code)

        status_set = set(responses)
        assert (
            429 in status_set
        ), f"Expected 429 in responses, got: {sorted(status_set)}"

    def test_auth_endpoints_not_rate_limited_by_default(
        self, client: TestClient
    ) -> None:
        """Auth endpoints with their own @limiter.limit() should use their
        specific limits (5/min or 3/min), not the 60/min default.

        The 5/min limit on /api/auth/token is stricter than 60/min,
        so it should trigger first.
        """
        responses = []
        for _ in range(10):
            resp = client.post(
                "/api/auth/token",
                json={"user_id": "nobody", "password": "wrong"},
            )
            responses.append(resp.status_code)

        status_set = set(responses)
        # With @limiter.limit("5/minute"), 10 rapid requests should trigger 429
        assert (
            429 in status_set
        ), f"Expected 429 for auth endpoint, got: {sorted(status_set)}"


@pytest.mark.slowapi
class TestRateLimitCleanup:
    """Verify that the rate limit counter resets after enough time."""

    @pytest.fixture(autouse=True)
    def _patch_auth(self):
        """Simulate enforced auth with a valid token for all test methods."""
        with (
            patch("src.auth.ENFORCE_AUTH", True),
            patch("src.auth.verify_token", return_value="alice"),
        ):
            yield

    @pytest.fixture(autouse=True)
    def _reset_limiter(self):
        """Clear the in-memory rate limiter storage between tests to avoid
        cross-test contamination.

        Since the TestClient shares the same app instance across tests,
        the limiter's in-memory storage accumulates hit counters. We
        reset it after each test method to ensure isolation.
        """
        yield
        from main_server import limiter

        limiter.reset()

    def _make_authed_post(
        self,
        client: TestClient,
        url: str,
        csrf_token: str = "rate-test-csrf",
        **kwargs,
    ):
        """Make a POST request with valid auth cookies and CSRF header."""
        client.cookies.clear()
        client.cookies.set("access_token", "valid-token")
        client.cookies.set("csrf_token", csrf_token)
        headers = kwargs.pop("headers", {})
        headers["X-CSRF-Token"] = csrf_token
        return client.post(url, headers=headers, **kwargs)

    def test_rate_limit_window_resets(
        self, client: TestClient
    ) -> None:
        """After the rate window passes, requests should succeed again.

        This test waits for the rate window (60 seconds) to pass and
        then verifies that a fresh request gets a 200 instead of 429.
        """
        # Burst: hit the limit
        hit_429 = False
        for _ in range(65):
            resp = self._make_authed_post(
                client, "/api/users/alice/sessions"
            )
            if resp.status_code == 429:
                hit_429 = True
                break

        if not hit_429:
            pytest.skip("Rate limit not triggered in initial burst")

        # Wait for the rate window to pass
        time.sleep(62)

        # Fresh request should succeed
        resp = self._make_authed_post(client, "/api/users/alice/sessions")
        assert resp.status_code == 200, (
            f"Expected 200 after rate window reset, got {resp.status_code}"
        )

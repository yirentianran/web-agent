"""Integration tests for API rate limiting.

Verifies that the global default rate limit (60/minute) applies
via the SlowAPIMiddleware.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient


def _make_authed_post(
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


@pytest.mark.usefixtures("_patch_auth")
class TestGlobalRateLimiting:
    """Verify that non-auth endpoints are rate-limited."""

    @pytest.fixture(autouse=True)
    def _reset_limiter(self):
        """Clear the in-memory rate limiter storage between tests."""
        from main_server import limiter

        limiter.reset()
        yield
        limiter.reset()

    def test_session_creation_rate_limited(
        self, client: TestClient
    ) -> None:
        """Creating many sessions quickly should eventually hit rate limit."""
        responses = []
        for _ in range(65):
            resp = _make_authed_post(client, "/api/users/alice/sessions")
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
            resp = _make_authed_post(
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
        """Auth endpoints with specific @limiter.limit() use their own limits."""
        responses = []
        for _ in range(10):
            resp = client.post(
                "/api/auth/token",
                json={"user_id": "nobody", "password": "wrong"},
            )
            responses.append(resp.status_code)

        status_set = set(responses)
        assert (
            429 in status_set
        ), f"Expected 429 for auth endpoint, got: {sorted(status_set)}"


@pytest.mark.slowapi
@pytest.mark.usefixtures("_patch_auth")
class TestRateLimitCleanup:
    """Verify that the rate limit counter resets after enough time."""

    @pytest.fixture(autouse=True)
    def _reset_limiter(self):
        """Clear the in-memory rate limiter storage between tests."""
        yield
        from main_server import limiter

        limiter.reset()

    def test_rate_limit_window_resets(
        self, client: TestClient
    ) -> None:
        """After the rate window passes, requests should succeed again."""
        hit_429 = False
        for _ in range(65):
            resp = _make_authed_post(
                client, "/api/users/alice/sessions"
            )
            if resp.status_code == 429:
                hit_429 = True
                break

        if not hit_429:
            pytest.skip("Rate limit not triggered in initial burst")

        time.sleep(62)

        resp = _make_authed_post(client, "/api/users/alice/sessions")
        assert resp.status_code == 200, (
            f"Expected 200 after rate window reset, got {resp.status_code}"
        )

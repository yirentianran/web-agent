"""Integration tests for CSRF protection on state-changing endpoints.

These tests are expected to FAIL initially because ``verify_csrf()`` is
not yet wired into any route handler dependencies.  Once Task 3 wires the
dependency, the tests should pass.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient


class TestCsrfProtection:
    """Verify CSRF protection is enforced on state-changing endpoints.

    These tests are expected to FAIL initially because verify_csrf() is
    not yet wired into any route handler dependencies.
    """

    def _create_session(self, client: TestClient) -> str:
        """Create a session and return its ID. Sets auth cookies first."""
        self._set_csrf_cookies(client)
        resp = client.post("/api/users/alice/sessions")
        assert resp.status_code == 200
        return resp.json()["session_id"]

    def _set_csrf_cookies(
        self, client: TestClient, csrf_token: str = "test-csrf-token"
    ) -> None:
        """Set both auth cookies on the test client."""
        client.cookies.set("access_token", "valid-token")
        client.cookies.set("csrf_token", csrf_token)

    @patch("src.auth.ENFORCE_AUTH", True)
    @patch("src.auth.verify_token", return_value="alice")
    def test_create_session_without_csrf_header_returns_403(
        self, mock_vt: object, client: TestClient
    ) -> None:
        """POST /api/users/alice/sessions without X-CSRF-Token should return 403."""
        self._set_csrf_cookies(client, "my-csrf-token")
        # Do NOT set X-CSRF-Token header -- should be rejected
        resp = client.post("/api/users/alice/sessions")
        assert resp.status_code == 403

    @patch("src.auth.ENFORCE_AUTH", True)
    @patch("src.auth.verify_token", return_value="alice")
    def test_delete_session_without_csrf_header_returns_403(
        self, mock_vt: object, client: TestClient
    ) -> None:
        """DELETE without X-CSRF-Token should return 403."""
        sid = self._create_session(client)
        self._set_csrf_cookies(client, "my-csrf-token")
        resp = client.delete(f"/api/users/alice/sessions/{sid}")
        assert resp.status_code == 403

    @patch("src.auth.ENFORCE_AUTH", True)
    @patch("src.auth.verify_token", return_value="alice")
    def test_upload_without_csrf_header_returns_403(
        self, mock_vt: object, client: TestClient
    ) -> None:
        """POST /api/users/alice/upload without X-CSRF-Token should return 403."""
        self._set_csrf_cookies(client, "my-csrf-token")
        resp = client.post(
            "/api/users/alice/upload",
            files={"file": ("test.txt", b"hello world", "text/plain")},
        )
        assert resp.status_code == 403

    @patch("src.auth.ENFORCE_AUTH", True)
    @patch("src.auth.verify_token", return_value="alice")
    def test_patch_title_without_csrf_header_returns_403(
        self, mock_vt: object, client: TestClient
    ) -> None:
        """PATCH without X-CSRF-Token should return 403."""
        sid = self._create_session(client)
        self._set_csrf_cookies(client, "my-csrf-token")
        resp = client.patch(
            f"/api/users/alice/sessions/{sid}/title",
            json={"title": "new title"},
        )
        assert resp.status_code == 403

    @patch("src.auth.ENFORCE_AUTH", True)
    @patch("src.auth.verify_token", return_value="alice")
    def test_state_change_with_valid_csrf_passes(
        self, mock_vt: object, client: TestClient
    ) -> None:
        """State-changing request with correct CSRF token should pass."""
        token = "valid-csrf-token"
        self._set_csrf_cookies(client, token)
        resp = client.post(
            "/api/users/alice/sessions",
            headers={"X-CSRF-Token": token},
        )
        assert resp.status_code == 200

    @patch("src.auth.ENFORCE_AUTH", True)
    @patch("src.auth.verify_token", return_value="alice")
    def test_state_change_with_wrong_csrf_token_returns_403(
        self, mock_vt: object, client: TestClient
    ) -> None:
        """State-changing request with wrong CSRF token should return 403."""
        self._set_csrf_cookies(client, "correct-token")
        resp = client.post(
            "/api/users/alice/sessions",
            headers={"X-CSRF-Token": "wrong-token"},
        )
        assert resp.status_code == 403

    def test_safe_methods_do_not_require_csrf(self, client: TestClient) -> None:
        """GET requests should work without CSRF tokens."""
        resp = client.get("/health")
        assert resp.status_code == 200

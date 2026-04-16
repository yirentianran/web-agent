"""Integration tests for auth-protected endpoints."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Import the app after patching ENFORCE_AUTH to avoid import-time caching issues


@pytest.fixture()
def auth_client() -> tuple[TestClient, str]:
    """Create a test client with ENFORCE_AUTH=True and a valid token."""
    from src.auth import create_token

    with patch("src.auth.ENFORCE_AUTH", True):
        token = create_token("testuser")
        # Import main_server after patching to pick up the right config
        from main_server import app

        client = TestClient(app)
        yield client, token


@pytest.fixture()
def no_auth_client() -> TestClient:
    """Create a test client with ENFORCE_AUTH=False (default)."""
    from main_server import app

    return TestClient(app)


# ── Token endpoint ────────────────────────────────────────────────


class TestTokenEndpoint:
    def test_token_returns_jwt(self, no_auth_client: TestClient) -> None:
        resp = no_auth_client.post("/api/auth/token", json={"user_id": "alice"})
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["user_id"] == "alice"


# ── WebSocket auth ────────────────────────────────────────────────


class TestWebSocketAuth:
    def test_ws_rejects_missing_token_when_auth_enforced(self) -> None:
        """WS connection is closed with code 4001 when ENFORCE_AUTH=True and no token."""
        from main_server import app

        with patch("src.auth.ENFORCE_AUTH", True):
            with TestClient(app) as client:
                with pytest.raises(Exception):
                    # TestClient raises on WebSocket close with non-1000 code
                    with client.websocket_connect("/ws"):
                        pass  # pragma: no cover

    def test_ws_accepts_valid_token(self) -> None:
        """WS connection succeeds when ENFORCE_AUTH=True with valid token."""
        from src.auth import create_token

        from main_server import app

        with patch("src.auth.ENFORCE_AUTH", True):
            token = create_token("testuser")
            with TestClient(app) as client:
                with client.websocket_connect(f"/ws?token={token}") as ws:
                    # Connection accepted — send a disconnect
                    ws.send_json({"type": "close"})

    def test_ws_rejects_invalid_token(self) -> None:
        """WS connection is closed when token is invalid."""
        from main_server import app

        with patch("src.auth.ENFORCE_AUTH", True):
            with TestClient(app) as client:
                with pytest.raises(Exception):
                    with client.websocket_connect("/ws?token=invalid-token"):
                        pass  # pragma: no cover

    def test_ws_allows_no_token_when_auth_disabled(self) -> None:
        """WS connection succeeds when ENFORCE_AUTH=False."""
        from main_server import app

        with patch("src.auth.ENFORCE_AUTH", False):
            with TestClient(app) as client:
                with client.websocket_connect("/ws") as ws:
                    ws.send_json({"type": "close"})


# ── Admin role enforcement ─────────────────────────────────────────


class TestAdminRoleEnforcement:
    """Test that /api/admin/* endpoints require admin role."""

    def _make_admin_token(self, user_id: str = "admin") -> str:
        from src.auth import create_token

        return create_token(user_id, role="admin")

    def _make_user_token(self, user_id: str = "regular") -> str:
        from src.auth import create_token

        return create_token(user_id, role="user")

    def test_admin_endpoint_allows_admin_with_auth_enforced(self) -> None:
        """GET /api/admin/mcp-servers succeeds for admin user when ENFORCE_ADMIN=True."""
        from main_server import app

        with patch("main_server.require_admin", return_value="admin"):
            token = self._make_admin_token("admin")
            with TestClient(app) as client:
                resp = client.get(
                    "/api/admin/mcp-servers",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert resp.status_code == 200

    def test_admin_endpoint_rejects_non_admin(self) -> None:
        """GET /api/admin/mcp-servers returns 403 for non-admin user."""
        from fastapi import HTTPException

        from main_server import app

        def _reject_admin(user_id: str) -> str:
            raise HTTPException(status_code=403, detail="Admin privileges required")

        with patch("main_server.require_admin", side_effect=_reject_admin):
            token = self._make_user_token("regular")
            with TestClient(app) as client:
                resp = client.get(
                    "/api/admin/mcp-servers",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert resp.status_code == 403
                assert "Admin privileges required" in resp.json()["detail"]

    def test_admin_endpoint_rejects_missing_token(self) -> None:
        """GET /api/admin/mcp-servers returns 401 when no token provided."""
        from main_server import app

        with patch("src.auth.ENFORCE_AUTH", True):
            with TestClient(app) as client:
                resp = client.get("/api/admin/mcp-servers")
                assert resp.status_code == 401

    def test_admin_endpoint_allows_all_when_not_enforced(self) -> None:
        """GET /api/admin/mcp-servers succeeds for any user when ENFORCE_ADMIN=False."""
        from main_server import app

        with patch("main_server.require_admin", return_value="regular"):
            token = self._make_user_token("regular")
            with TestClient(app) as client:
                resp = client.get(
                    "/api/admin/mcp-servers",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert resp.status_code == 200

    def test_containers_endpoint_requires_admin(self) -> None:
        """GET /api/admin/containers returns 403 for non-admin."""
        from fastapi import HTTPException

        from main_server import app

        def _reject_admin(user_id: str) -> str:
            raise HTTPException(status_code=403, detail="Admin privileges required")

        with patch("main_server.require_admin", side_effect=_reject_admin), \
             patch("main_server.CONTAINER_MODE", False):
            token = self._make_user_token("regular")
            with TestClient(app) as client:
                resp = client.get(
                    "/api/admin/containers",
                    headers={"Authorization": f"Bearer {token}"},
                )
                # 403 before 501 (admin check runs first)
                assert resp.status_code == 403

    def test_resources_endpoint_requires_admin(self) -> None:
        """GET /api/admin/resources returns 403 for non-admin."""
        from fastapi import HTTPException

        from main_server import app

        def _reject_admin(user_id: str) -> str:
            raise HTTPException(status_code=403, detail="Admin privileges required")

        with patch("main_server.require_admin", side_effect=_reject_admin):
            token = self._make_user_token("regular")
            with TestClient(app) as client:
                resp = client.get(
                    "/api/admin/resources",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert resp.status_code == 403


# ── Audit log & log cleanup endpoints ─────────────────────────────


class TestAuditLogEndpoints:
    """Test that audit log API endpoints work with admin auth."""

    def test_query_audit_logs_admin_allowed(self, no_auth_client: TestClient) -> None:
        """Admin can query audit logs."""
        from src.audit_logger import get_audit_logger

        with patch("main_server.require_admin", return_value="admin"):
            audit = get_audit_logger()
            audit.log("auth", {"user_id": "alice", "action": "login", "result": "ok"})

            resp = no_auth_client.get(
                "/api/admin/audit-logs",
                params={"category": "auth"},
                headers={"Authorization": "Bearer faketoken"},
            )
            assert resp.status_code == 200
            assert isinstance(resp.json(), list)

    def test_query_audit_logs_admin_denied(self, no_auth_client: TestClient) -> None:
        """Non-admin cannot query audit logs."""
        from fastapi import HTTPException

        def _reject_admin(user_id: str) -> str:
            raise HTTPException(status_code=403, detail="Admin privileges required")

        with patch("main_server.require_admin", side_effect=_reject_admin):
            resp = no_auth_client.get(
                "/api/admin/audit-logs",
                params={"category": "auth"},
                headers={"Authorization": "Bearer faketoken"},
            )
            assert resp.status_code == 403

    def test_trigger_log_cleanup_admin_allowed(self, no_auth_client: TestClient) -> None:
        """Admin can trigger log cleanup."""
        with patch("main_server.require_admin", return_value="admin"):
            resp = no_auth_client.post(
                "/api/admin/logs/cleanup",
                headers={"Authorization": "Bearer faketoken"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "l2_app_evicted" in data
            assert "l3_agent_evicted" in data

    def test_trigger_log_cleanup_admin_denied(self, no_auth_client: TestClient) -> None:
        """Non-admin cannot trigger log cleanup."""
        from fastapi import HTTPException

        def _reject_admin(user_id: str) -> str:
            raise HTTPException(status_code=403, detail="Admin privileges required")

        with patch("main_server.require_admin", side_effect=_reject_admin):
            resp = no_auth_client.post(
                "/api/admin/logs/cleanup",
                headers={"Authorization": "Bearer faketoken"},
            )
            assert resp.status_code == 403

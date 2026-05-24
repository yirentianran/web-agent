"""Tests for /api/admin/users endpoints."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Prevent SDK from being imported (it's not installed in test environments)
_mock_sdk = MagicMock()
_mock_sdk.types = MagicMock()
_mock_sdk.types.UserMessage = MagicMock
sys.modules["claude_agent_sdk"] = _mock_sdk
sys.modules["claude_agent_sdk.types"] = _mock_sdk.types

from fastapi.testclient import TestClient

import main_server
import src.auth
import src.admin_auth

src.auth.ENFORCE_AUTH = False
src.admin_auth.ENFORCE_AUTH = False

from src.database import Database


@pytest.fixture(autouse=True)
async def _patch_data_root_and_db(tmp_path: Path) -> None:
    """Redirect DATA_ROOT and initialize a fresh test database with seed users."""
    main_server.DATA_ROOT = tmp_path
    main_server.buffer = main_server.MessageBuffer()
    main_server.active_tasks.clear()
    main_server.pending_answers.clear()
    (tmp_path / "users").mkdir(exist_ok=True)

    # Initialize a fresh test DB
    db = Database(db_path=tmp_path / "test.db")
    await db.init()
    await db.migrate_v2()
    main_server._db = db
    main_server.buffer.db = db

    # Seed test users
    async with db.connection() as conn:
        await conn.execute(
            """INSERT INTO users (user_id, role, status, created_at, last_active_at)
               VALUES (?, ?, ?, 1735056000, 1735056000)""",
            ["admin_user", "admin", "active"],
        )
        await conn.execute(
            """INSERT INTO users (user_id, role, status, created_at, last_active_at)
               VALUES (?, ?, ?, 1735056000, 1735056000)""",
            ["regular_user", "user", "active"],
        )
        await conn.execute(
            """INSERT INTO users (user_id, role, status, created_at, last_active_at)
               VALUES (?, ?, ?, 1735056000, 1735142400)""",
            ["disabled_user", "user", "disabled"],
        )
        await conn.execute(
            """INSERT INTO users (user_id, role, status, created_at, last_active_at)
               VALUES (?, ?, ?, 1735056000, 1735056000)""",
            ["testuser", "user", "active"],
        )

    yield

    await db.close()
    main_server._db = None
    main_server.buffer.db = None


@pytest.fixture()
def client() -> TestClient:
    return TestClient(main_server.app)


def test_list_users_returns_paginated_items(client: TestClient):
    resp = client.get("/api/admin/users?page=1&page_size=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "items" in body["data"]
    assert "total" in body["data"]
    assert body["data"]["page"] == 1
    assert body["data"]["page_size"] == 10


def test_list_users_search_by_user_id(client: TestClient):
    resp = client.get("/api/admin/users?q=admin")
    assert resp.status_code == 200
    body = resp.json()
    for item in body["data"]["items"]:
        assert "admin" in item["user_id"].lower()


def test_list_users_filter_by_role(client: TestClient):
    resp = client.get("/api/admin/users?role=admin")
    assert resp.status_code == 200
    for item in resp.json()["data"]["items"]:
        assert item["role"] == "admin"


def test_list_users_filter_by_status(client: TestClient):
    resp = client.get("/api/admin/users?status=disabled")
    assert resp.status_code == 200
    for item in resp.json()["data"]["items"]:
        assert item["status"] == "disabled"


def test_list_users_sort_by_last_active(client: TestClient):
    resp = client.get("/api/admin/users?sort=last_active_at&order=desc&page_size=50")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    if len(items) >= 2:
        assert items[0]["last_active_at"] >= items[-1]["last_active_at"]


def test_list_users_rejects_invalid_sort_column(client: TestClient):
    resp = client.get("/api/admin/users?sort=password_hash&order=asc")
    assert resp.status_code == 400


def test_disable_user_sets_status(client: TestClient):
    resp = client.post("/api/admin/users/testuser/disable")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["status"] == "disabled"
    assert body["data"]["disabled_by"] is not None
    assert body["data"]["disabled_at"] is not None


def test_disable_user_already_disabled_returns_409(client: TestClient):
    # first disable, then second call on already-disabled user
    client.post("/api/admin/users/testuser/disable")
    resp = client.post("/api/admin/users/testuser/disable")
    assert resp.status_code == 409


def test_disable_self_returns_403(client: TestClient):
    resp = client.post("/api/admin/users/default/disable")
    assert resp.status_code == 403


def test_enable_user_clears_disabled_fields(client: TestClient):
    # first disable, then enable
    client.post("/api/admin/users/testuser/disable")
    resp = client.post("/api/admin/users/testuser/enable")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["status"] == "active"
    assert body["data"]["disabled_at"] is None
    assert body["data"]["disabled_by"] is None


def test_enable_user_already_active_returns_409(client: TestClient):
    resp = client.post("/api/admin/users/testuser/enable")
    assert resp.status_code == 409


def test_non_admin_rejected(client: TestClient):
    src.admin_auth.ENFORCE_AUTH = True
    try:
        resp = client.get("/api/admin/users")
        assert resp.status_code in (401, 403)
    finally:
        src.admin_auth.ENFORCE_AUTH = False

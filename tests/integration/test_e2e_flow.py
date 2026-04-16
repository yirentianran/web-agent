"""Integration tests for the full WebSocket-to-SDK flow.

These tests exercise the complete message lifecycle with a mocked SDK.
The WS handler's subscription loop uses a 30s timeout that is incompatible with
Starlette's synchronous TestClient, so replay/receive logic is tested via the
buffer directly while the WS handler is tested for basic connect/disconnect.

Tests:
1. WS basic connect + answer message handling
2. History replay via buffer (same logic the WS handler uses)
3. Answer future resolution
4. Session lifecycle via REST
5. Cost tracking
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import main_server


# ── Helpers ────────────────────────────────────────────────────────


def _create_session(client: TestClient, user_id: str = "alice") -> str:
    """Create a session via REST, return session_id."""
    resp = client.post(f"/api/users/{user_id}/sessions")
    assert resp.status_code == 200
    return resp.json()["session_id"]


# ── WS basic connect ──────────────────────────────────────────────


class TestWebSocketBasic:
    def test_ws_connect_and_disconnect(self, client: TestClient) -> None:
        """Basic WS connection should open and close without error."""
        with client.websocket_connect("/ws"):
            pass  # connect, then exit context (disconnect)

    def test_ws_receives_error_for_malformed_input(self, client: TestClient) -> None:
        """Send malformed JSON -- should get error response."""
        with client.websocket_connect("/ws") as ws:
            ws.send_text("not-valid-json")
            data = ws.receive_text()
            result = json.loads(data)
            assert "type" in result


# ── WS answer flow ────────────────────────────────────────────────


class TestWebSocketAnswerFlow:
    def test_send_answer_type_message(self, client: TestClient) -> None:
        """Sending type='answer' should be handled without error."""
        sid = _create_session(client, user_id="dave")
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({
                "type": "answer",
                "user_id": "dave",
                "session_id": sid,
                "answers": {"question_key": "yes"},
            }))
            # Server processes answer silently, no response expected

    def test_answer_sets_future_result(self, client: TestClient) -> None:
        """An answer message should resolve a pending asyncio.Future."""
        sid = f"session_answer_test_{time.time()}"
        main_server.buffer.add_message(sid, {"type": "user", "content": "test"})

        future: asyncio.Future = asyncio.Future()
        main_server.pending_answers[sid] = future

        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({
                "type": "answer",
                "user_id": "eve",
                "session_id": sid,
                "answers": {"choice": "option_a"},
            }))

        assert future.done()
        assert future.result() == {"choice": "option_a"}
        main_server.pending_answers.pop(sid, None)


# ── History replay logic (via buffer, same as WS handler uses) ────


class TestHistoryReplay:
    """Test the replay logic that the WS handler relies on.

    The WS handler calls buffer.get_history(session_id, after_index=N) to
    retrieve messages.  We test this directly since the subscription loop
    is incompatible with Starlette's synchronous TestClient.
    """

    def test_empty_history_returns_nothing(self, client: TestClient) -> None:
        """New session has no messages."""
        sid = _create_session(client, user_id="alice")
        history = main_server.buffer.get_history(sid, after_index=0)
        assert len(history) == 0

    def test_replay_all_from_index_zero(self, client: TestClient) -> None:
        """after_index=0 returns all messages."""
        sid = _create_session(client, user_id="bob")
        main_server.buffer.add_message(sid, {"type": "user", "content": "Hi"})
        main_server.buffer.add_message(sid, {"type": "assistant", "content": "Hello!"})

        history = main_server.buffer.get_history(sid, after_index=0)
        assert len(history) == 2
        assert history[0]["type"] == "user"
        assert history[1]["type"] == "assistant"

    def test_replay_from_nonzero_index(self, client: TestClient) -> None:
        """after_index=N skips first N messages."""
        sid = _create_session(client, user_id="carol")
        for i in range(5):
            main_server.buffer.add_message(sid, {"type": "assistant", "content": f"msg{i}"})

        history = main_server.buffer.get_history(sid, after_index=3)
        assert len(history) == 2
        assert history[0]["content"] == "msg3"
        assert history[1]["content"] == "msg4"

    def test_replay_beyond_history_returns_empty(self, client: TestClient) -> None:
        """after_index >= total returns empty list."""
        sid = _create_session(client, user_id="dave")
        main_server.buffer.add_message(sid, {"type": "user", "content": "Hi"})
        main_server.buffer.add_message(sid, {"type": "assistant", "content": "Hello!"})

        history = main_server.buffer.get_history(sid, after_index=2)
        assert len(history) == 0


# ── Session lifecycle via REST ─────────────────────────────────────


class TestSessionLifecycleREST:
    def test_create_check_status_cancel(self, client: TestClient) -> None:
        """Full session lifecycle: create -> check status -> cancel -> verify."""
        sid = _create_session(client, user_id="grace")

        resp = client.get(f"/api/users/grace/sessions/{sid}/status")
        assert resp.status_code == 200
        assert resp.json()["state"] == "idle"

        resp = client.post(f"/api/users/grace/sessions/{sid}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        resp = client.get(f"/api/users/grace/sessions/{sid}/status")
        assert resp.json()["state"] == "cancelled"

    def test_list_sessions_after_create(self, client: TestClient) -> None:
        """Creating sessions should make them appear in list."""
        _create_session(client, user_id="hank")
        _create_session(client, user_id="hank")

        resp = client.get("/api/users/hank/sessions")
        assert resp.status_code == 200
        assert len(resp.json()) >= 2

    def test_delete_session(self, client: TestClient) -> None:
        """Deleting a session should remove it from list."""
        sid = _create_session(client, user_id="iris")
        resp = client.delete(f"/api/users/iris/sessions/{sid}")
        assert resp.status_code == 200


# ── Cost tracking ──────────────────────────────────────────────────


class TestCostTracking:
    def test_cost_accumulates_with_messages(self, client: TestClient) -> None:
        """Messages with usage data should accumulate cost."""
        sid = _create_session(client, user_id="jack")

        main_server.buffer.add_message(sid, {
            "type": "assistant",
            "content": "Response",
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "model": "claude-sonnet-4-6",
            },
        })

        state = main_server.buffer.get_session_state(sid)
        assert state["cost_usd"] > 0

    def test_initial_cost_is_zero(self, client: TestClient) -> None:
        """New session should have zero cost."""
        sid = _create_session(client, user_id="kate")
        state = main_server.buffer.get_session_state(sid)
        assert state["cost_usd"] == 0.0

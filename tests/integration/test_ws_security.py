"""Integration tests for WebSocket security enforcement."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.auth import create_token


class TestWebSocketUserEnforcement:
    """Test that WS messages with mismatched user_id are rejected."""

    @patch("src.auth.ENFORCE_AUTH", True)
    def test_message_with_mismatched_user_id_is_rejected(
        self, client: TestClient
    ) -> None:
        """WS message from user A claiming to be user B should be rejected."""
        token = create_token("alice")
        with client.websocket_connect(f"/ws?token={token}") as ws:
            # Send message claiming to be user "bob"
            ws.send_text(json.dumps({
                "type": "user",
                "user_id": "bob",
                "content": "hello",
            }))
            response = ws.receive_text()
            data = json.loads(response)
            assert data.get("type") == "error" or data.get("error") is not None

    @patch("src.auth.ENFORCE_AUTH", True)
    def test_message_with_matching_user_id_is_accepted(
        self, client: TestClient
    ) -> None:
        """WS message with matching user_id should be processed."""
        token = create_token("alice")
        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.send_text(json.dumps({
                "type": "user",
                "user_id": "alice",
                "content": "hello",
            }))
            ws.send_text(json.dumps({"type": "ping"}))

    @patch("src.auth.ENFORCE_AUTH", True)
    def test_ws_without_token_is_rejected(self, client: TestClient) -> None:
        """WS connection without token should be closed with code 4001."""
        with pytest.raises(Exception):
            with client.websocket_connect("/ws") as ws:
                ws.receive_text()

    @patch("src.auth.ENFORCE_AUTH", True)
    def test_ws_with_invalid_token_is_rejected(self, client: TestClient) -> None:
        """WS connection with invalid token should be closed."""
        with pytest.raises(Exception):
            with client.websocket_connect("/ws?token=invalid.fake.token") as ws:
                ws.receive_text()

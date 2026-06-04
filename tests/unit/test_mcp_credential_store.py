"""Tests for MCP credential encryption at rest."""

from __future__ import annotations

import os
import json
import base64
from unittest.mock import MagicMock, patch

import pytest

# We'll test the encryption functions directly from mcp_store
from src import mcp_store
from src.mcp_store import MCPServerStore


class TestCredentialEncryption:
    """Verify MCP headers and env values are encrypted at rest."""

    def setup_method(self):
        self.mock_db = MagicMock()
        self.mock_db.connection = MagicMock()

    def test_encryption_module_has_expected_api(self) -> None:
        """Verify mcp_store has encryption functions."""
        assert hasattr(mcp_store, "_encrypt_sensitive_fields")
        assert hasattr(mcp_store, "_decrypt_sensitive_fields")
        assert hasattr(mcp_store, "_encryption_available")

    def test_encrypt_decrypt_roundtrip(self) -> None:
        """Encrypted values should decrypt back to original."""
        with patch.dict(os.environ, {"MCP_ENCRYPTION_KEY": "test-key-32-bytes-long!!"}):
            import importlib
            importlib.reload(mcp_store)

            if mcp_store._encryption_available:
                data = {
                    "headers": {"Authorization": "Bearer sk-secret-key-12345"},
                    "env": {"API_KEY": "secret-value"},
                }
                encrypted = mcp_store._encrypt_sensitive_fields(dict(data))
                # Verify encrypted is different
                assert encrypted["headers"] != data["headers"]
                assert encrypted["env"] != data["env"]
                # Verify encryption produces strings (base64)
                assert isinstance(encrypted["headers"], str)
                assert isinstance(encrypted["env"], str)
                # Verify decryption restores original
                decrypted = mcp_store._decrypt_sensitive_fields(encrypted)
                assert decrypted == data

    def test_plaintext_passthrough_when_no_key(self) -> None:
        """Without MCP_ENCRYPTION_KEY, data passes through unchanged."""
        with patch.dict(os.environ, {}, clear=True):
            import importlib
            importlib.reload(mcp_store)

            data = {"headers": {"X-Test": "value"}, "env": {}}
            result = mcp_store._encrypt_sensitive_fields(dict(data))
            assert result == data

    def test_decrypt_plaintext_is_safe(self) -> None:
        """Decrypting already-plaintext data should not crash."""
        with patch.dict(os.environ, {}, clear=True):
            import importlib
            importlib.reload(mcp_store)

            # Plain text headers (as they'd be read from old DB)
            data = {"headers": {"Authorization": "Bearer old-key"}, "env": {}}
            result = mcp_store._decrypt_sensitive_fields(dict(data))
            assert result == data

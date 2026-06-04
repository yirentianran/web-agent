"""Helpers for security audit / penetration testing."""

from __future__ import annotations


def forge_request(method: str, path: str, headers: dict = None, body: dict = None) -> dict:
    """Create a forged HTTP request for penetration testing."""
    return {
        "method": method.upper(),
        "path": path,
        "headers": headers or {},
        "body": body or {},
    }


def impersonate_user(victim_user_id: str, attacker_token: str) -> dict:
    """Create headers that attempt to impersonate another user."""
    return {
        "Cookie": f"access_token={attacker_token}",
        "X-User-Impersonate": victim_user_id,
    }

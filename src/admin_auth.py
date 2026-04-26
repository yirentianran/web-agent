"""No-op admin guard — all users have equal privileges.

Previously enforced admin-only access via ENFORCE_ADMIN / ADMIN_USER_IDS
environment variables. That distinction has been removed per the simplified
design: there is no admin role, every user has the same permissions.
"""

from __future__ import annotations


def require_admin(user_id: str) -> str:
    """Passthrough — always returns *user_id* unchanged."""
    return user_id

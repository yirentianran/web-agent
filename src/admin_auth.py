"""Admin role enforcement for protected endpoints."""

from __future__ import annotations

import os

from fastapi import HTTPException, status

ENFORCE_ADMIN = os.getenv("ENFORCE_ADMIN", "false").lower() == "true"
ADMIN_USER_IDS = os.getenv("ADMIN_USER_IDS", "admin").split(",")


def require_admin(user_id: str) -> str:
    """Check if the user has admin privileges.

    When ENFORCE_ADMIN is False, always returns user_id (passthrough).
    When ENFORCE_ADMIN is True, raises 403 if user_id is not in ADMIN_USER_IDS.
    Logs every admin check to the audit log.
    """
    if not ENFORCE_ADMIN:
        return user_id
    if user_id not in ADMIN_USER_IDS:
        from src.audit_logger import get_audit_logger

        get_audit_logger().log(
            "admin",
            {
                "user_id": user_id,
                "action": "require_admin",
                "result": "denied",
                "detail": "User not in ADMIN_USER_IDS",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    from src.audit_logger import get_audit_logger

    get_audit_logger().log(
        "admin",
        {"user_id": user_id, "action": "require_admin", "result": "allowed"},
    )
    return user_id

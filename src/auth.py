"""JWT authentication module.

Provides token creation, verification, and FastAPI dependency for user auth.
"""

from __future__ import annotations

import os
import warnings
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException, Query, status

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours
ENFORCE_AUTH = os.getenv("ENFORCE_AUTH", "false").lower() == "true"

_SECRET = os.getenv("JWT_SECRET", "")

if ENFORCE_AUTH and not _SECRET:
    warnings.warn(
        "JWT_SECRET is not set but ENFORCE_AUTH=true. "
        "Using a random secret -- tokens will not survive server restarts!",
        RuntimeWarning,
        stacklevel=2,
    )
    _SECRET = os.urandom(32).hex()

JWT_SECRET = _SECRET or "dev-secret-change-in-production-use-at-least-32-chars"


def create_token(
    user_id: str,
    role: str = "user",
    expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES,
) -> str:
    """Create a JWT access token for the given user_id."""
    from src.audit_logger import get_audit_logger

    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)
    get_audit_logger().log(
        "auth",
        {"user_id": user_id, "action": "token_create", "role": role, "result": "ok"},
    )
    return token


def verify_token(token: str) -> str:
    """Verify a JWT token and return the user_id (sub claim).

    Raises HTTPException(401) on invalid, expired, or tampered tokens.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    user_id: str | None = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing user claim",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id


def get_current_user(
    token: str | None = Query(None, description="JWT access token"),
) -> str:
    """FastAPI dependency: extract and verify user from JWT token.

    When ENFORCE_AUTH is False, returns "default" without requiring a token.
    """
    if not ENFORCE_AUTH:
        return "default"

    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return verify_token(token)


def require_user_match(path_user_id: str, current_user: str) -> str:
    """Verify that the authenticated user matches the path parameter.

    Returns the user_id if they match, raises 403 otherwise.
    When ENFORCE_AUTH is False, always returns path_user_id (passthrough).
    """
    if not ENFORCE_AUTH:
        return path_user_id
    if path_user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access another user's resources",
        )
    return current_user

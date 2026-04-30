"""JWT authentication module.

Provides token creation, verification, password hashing, and FastAPI dependency for user auth.
"""

from __future__ import annotations

import os
import warnings
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import HTTPException, Query, Header, status

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


def hash_password(password: str) -> str:
    """Hash a password with bcrypt. Returns the hashed string."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def create_token(
    user_id: str,
    role: str = "user",
    expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES,
) -> str:
    """Create a JWT access token for the given user_id."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


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
    authorization: str | None = Header(None),
    token: str | None = Query(None, description="JWT access token (fallback)"),
) -> str:
    """FastAPI dependency: extract and verify user from JWT token.

    Accepts tokens from two sources, checked in priority order:
    1. Authorization: Bearer <token> header (primary)
    2. ?token=<jwt> query parameter (fallback, for WebSocket)

    When ENFORCE_AUTH is False, returns "default" without requiring a token.
    """
    if not ENFORCE_AUTH:
        return "default"

    raw_token: str | None = None
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization.split(" ", 1)[1]
    elif token:
        raw_token = token

    if raw_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return verify_token(raw_token)


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


def verify_path_user(path_user_id: str, current_user: str) -> str:
    """Verify path user_id matches current_user. Convenience wrapper.

    Returns current_user on success, raises 403 on mismatch.
    In dev mode (ENFORCE_AUTH=false), returns path_user_id.
    """
    return require_user_match(path_user_id, current_user)

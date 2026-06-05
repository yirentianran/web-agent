"""Admin auth — JWT role-based admin verification.

Supports both Authorization header (Bearer token) and httpOnly cookie
(access_token) for admin authentication.
"""

from __future__ import annotations

import os

import jwt
from fastapi import Cookie, Header, HTTPException, status

ALGORITHM = "HS256"
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-production-use-at-least-32-chars")
ENFORCE_AUTH = os.getenv("ENFORCE_AUTH", "false").lower() == "true"
ACCESS_TOKEN_COOKIE = "access_token"


def _verify_admin_token(token: str) -> str:
    """Decode a JWT and verify the admin role. Returns user_id."""
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

    role: str | None = payload.get("role")
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )

    user_id: str | None = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing user claim",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id


def require_admin(
    authorization: str | None = Header(None),
    access_token: str | None = Cookie(None, alias=ACCESS_TOKEN_COOKIE),
) -> str:
    """FastAPI dependency: verify the request has a valid admin JWT token.

    Checks the ``Authorization: Bearer <token>`` header first, then falls
    back to the ``access_token`` httpOnly cookie.  Raises 401 if the token
    is missing / invalid, or 403 if the user is not an admin.

    When ``ENFORCE_AUTH`` is False returns ``"default"`` (dev passthrough).
    """
    if not ENFORCE_AUTH:
        return "default"

    # Collect candidate tokens — cookie first (always fresh), then header
    candidates: list[str] = []
    if access_token:
        candidates.append(access_token)
    if authorization and authorization.startswith("Bearer "):
        hdr_token = authorization.split(" ", 1)[1]
        if hdr_token not in candidates:
            candidates.append(hdr_token)

    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing admin authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Try each candidate — the first valid admin token wins
    last_error = None
    for token in candidates:
        try:
            return _verify_admin_token(token)
        except HTTPException as e:
            last_error = e
    raise last_error  # type: ignore[misc]


def is_admin_request(
    authorization: str | None = None,
    access_token: str | None = None,
) -> bool:
    """Return True if the request carries a valid admin JWT. Never raises."""
    if not ENFORCE_AUTH:
        return True

    # Cookie first (httpOnly, always fresh), then header fallback
    candidates: list[str] = []
    if access_token:
        candidates.append(access_token)
    if authorization and authorization.startswith("Bearer "):
        hdr_token = authorization.split(" ", 1)[1]
        if hdr_token not in candidates:
            candidates.append(hdr_token)
    if not candidates:
        return False

    for token in candidates:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
            return payload.get("role") == "admin"
        except Exception:
            continue
    return False

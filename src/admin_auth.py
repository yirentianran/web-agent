"""Admin auth — JWT role-based admin verification.

Usage:
    from src.admin_auth import require_admin

    @app.get("/admin/endpoint")
    async def admin_endpoint(current_user: str = Depends(require_admin)):
        ...
"""

from __future__ import annotations

import os

import jwt
from fastapi import Depends, Header, HTTPException, status

ALGORITHM = "HS256"
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-production-use-at-least-32-chars")
ENFORCE_AUTH = os.getenv("ENFORCE_AUTH", "false").lower() == "true"


def require_admin(authorization: str | None = Header(None)) -> str:
    """Verify the request has a valid admin JWT token.

    Returns the user_id from the token if admin role is confirmed.
    Raises 401 if token is missing/invalid, 403 if user is not admin.

    When ENFORCE_AUTH is False, returns "default" (dev mode passthrough).
    """
    if not ENFORCE_AUTH:
        return "default"

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing admin authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.split(" ", 1)[1]
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

"""JWT authentication module.

Provides token creation, verification, password hashing, and FastAPI dependency for user auth.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import HTTPException, Request, Response, status

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days
ENFORCE_AUTH = os.getenv("ENFORCE_AUTH", "false").lower() == "true"

_SECRET = os.getenv("JWT_SECRET", "")

if ENFORCE_AUTH and not _SECRET:
    raise RuntimeError(
        "ENFORCE_AUTH=true but JWT_SECRET is not set. "
        "Set JWT_SECRET to a strong random value (at least 32 chars) to enable authentication."
    )

JWT_SECRET = _SECRET or os.urandom(32).hex()


def hash_password(password: str) -> str:
    """Hash a password with bcrypt. Returns the hashed string."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash."""
    if not hashed or not password:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


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



def _decode_payload(token: str) -> dict:
    """Decode and verify a JWT, returning the full payload. Raises HTTPException on failure."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
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


def get_current_user(request: Request, response: Response | None = None) -> str:
    """FastAPI dependency: extract and verify user from httpOnly JWT cookie.

    When ENFORCE_AUTH is False, tries to validate a token if present,
    returning empty string when no token is provided.

    If the token is valid but expiring soon, automatically issues a new
    token to extend the session (sliding expiration).
    """
    raw_token: str | None = request.cookies.get(ACCESS_TOKEN_COOKIE)

    if not ENFORCE_AUTH:
        if raw_token is not None:
            try:
                payload = _decode_payload(raw_token)
                return _maybe_renew(raw_token, payload, response)
            except HTTPException:
                pass
        return ""

    if raw_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = _decode_payload(raw_token)
    return _maybe_renew(raw_token, payload, response)


def _maybe_renew(token: str, payload: dict, response: Response | None) -> str:
    """Issue a fresh token on every authenticated request (sliding expiration)."""
    user_id: str = payload.get("sub", "")
    if not user_id:
        return user_id

    if response is not None:
        role: str = payload.get("role", "user")
        new_token = create_token(user_id, role)
        set_auth_cookies(response, new_token)

    return user_id


def require_user_match(path_user_id: str, current_user: str) -> str:
    """Verify that the authenticated user matches the path parameter.

    Returns the user_id if they match, raises 403 otherwise.
    When ENFORCE_AUTH is False, allows passthrough.
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


# ── CSRF Protection ───────────────────────────────────────────────────

ACCESS_TOKEN_COOKIE = "access_token"
CSRF_TOKEN_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def create_csrf_token() -> str:
    """Generate a cryptographically random CSRF token."""
    return secrets.token_hex(32)


def set_auth_cookies(response, access_token: str) -> str:
    """Set httpOnly access_token cookie and readable csrf_token cookie.

    Returns the new CSRF token value (also set as a cookie).
    Callers can include it in the response body if needed.
    """
    secure = ENFORCE_AUTH  # only require HTTPS when auth is enforced (prod)
    csrf_token = create_csrf_token()
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=access_token,
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        key=CSRF_TOKEN_COOKIE,
        value=csrf_token,
        httponly=False,  # readable by JS to include in X-CSRF-Token header
        secure=secure,
        samesite="strict",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    return csrf_token


def clear_auth_cookies(response) -> None:
    """Delete both auth cookies (used on logout)."""
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/")
    response.delete_cookie(CSRF_TOKEN_COOKIE, path="/")


def verify_csrf(request: Request) -> None:
    """Verify the X-CSRF-Token header matches the csrf_token cookie.

    Skipped for safe methods (GET, HEAD, OPTIONS) and when ENFORCE_AUTH is False.
    Raises HTTPException(403) on mismatch.
    """
    if not ENFORCE_AUTH:
        return
    if request.method.upper() in SAFE_METHODS:
        return

    cookie_csrf = request.cookies.get(CSRF_TOKEN_COOKIE, "")
    header_csrf = request.headers.get(CSRF_HEADER, "")

    if not cookie_csrf or not header_csrf or cookie_csrf != header_csrf:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing or invalid",
        )


def get_current_user_from_cookie(request: Request) -> str:
    """FastAPI dependency: extract user from httpOnly access_token cookie.

    Falls back to Bearer header for backward compatibility.
    When ENFORCE_AUTH is False, returns empty string when no token is present.
    """
    raw_token: str | None = request.cookies.get(ACCESS_TOKEN_COOKIE)

    # Fallback: Authorization header
    if not raw_token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            raw_token = auth_header.split(" ", 1)[1]

    if not ENFORCE_AUTH:
        if raw_token:
            try:
                return verify_token(raw_token)
            except HTTPException:
                pass
        return ""

    if raw_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return verify_token(raw_token)

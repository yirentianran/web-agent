"""Security headers middleware for FastAPI/Starlette.

Injects Content-Security-Policy, HSTS, X-Frame-Options, and other
security headers on every HTTP response.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject security headers on all responses."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response: Response = await call_next(request)
        headers = response.headers
        headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
            "font-src 'self'; connect-src 'self'; frame-src 'none'; "
            "object-src 'none'; base-uri 'self'",
        )
        return response

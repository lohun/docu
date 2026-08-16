"""Signed double-submit CSRF protection.

The API authenticates via httpOnly cookies. Because the API is consumed by
frontends on other domains, those cookies must be SameSite=None; Secure when
deployed, which provides no CSRF defence on its own. We therefore enforce
double-submit: the server signs a random value into a JS-readable cookie and
every state-changing request must echo that exact value in ``X-CSRF-Token``.
A cross-site attacker's origin cannot read the cookie value, so it cannot forge
the header. The value is signed with the server's JWT secret so a network
attacker who can inject a cookie cannot pass a crafted value either.
"""

import hashlib
import hmac
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import get_settings

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "X-CSRF-Token"

# Stable name across environments (host-only cookie; __Host- adds no protection).
CSRF_COOKIE = "csrf_token"


def csrf_cookie_name() -> str:
    return CSRF_COOKIE


def _mac(token: str) -> str:
    return hmac.new(
        get_settings().jwt_secret_key.encode(),
        token.encode(),
        hashlib.sha256,
    ).hexdigest()


def issue_csrf_value() -> str:
    token = secrets.token_urlsafe(32)
    return f"{token}.{_mac(token)}"


def validate_csrf_value(value: str | None) -> bool:
    if not value or "." not in value:
        return False
    token, sep, mac = value.rpartition(".")
    if not sep or not token:
        return False
    return hmac.compare_digest(mac, _mac(token))


def set_csrf_cookie(response: Response) -> None:
    settings = get_settings()
    token = issue_csrf_value()
    response.set_cookie(
        key=csrf_cookie_name(),
        value=token,
        httponly=False,  # JS must read it to echo back as the header
        secure=settings.cookie_secure_enabled,
        samesite=settings.cookie_samesite_value,
        path="/",
        max_age=settings.refresh_token_expire_days * 86400,
    )
    return token


class CSRFMiddleware(BaseHTTPMiddleware):
    """Enforce signed double-submit on every state-changing request.

    Safe methods pass through untouched so the explicit ``GET /auth/csrf``
    handshake (plus login/refresh responses) is the only place the cookie is
    issued -- any state-changing request without a prior handshake is rejected.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in SAFE_METHODS:
            return await call_next(request)

        cookie_value = request.cookies.get(csrf_cookie_name())
        header_value = request.headers.get(CSRF_HEADER)
        if (
            not validate_csrf_value(cookie_value)
            or not hmac.compare_digest(header_value or "", cookie_value or "")
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "invalid or missing CSRF token"},
            )
        return await call_next(request)
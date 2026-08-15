from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import get_settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Set progressive hardening headers on every response.

    HSTS is only emitted outside development (browsers ignore it on plain http
    anyway). Auth responses must never be cached by shared/proxy caches.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not settings.is_development:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        if request.url.path.startswith("/auth/"):
            response.headers["Cache-Control"] = "no-store"
        return response


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


import ipaddress
import socket
from urllib.parse import urlparse


class SSRFError(Exception):
    pass


BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def is_ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return True
    return any(ip in net for net in BLOCKED_NETWORKS)


def validate_target_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFError(f"unsupported URL scheme '{parsed.scheme}'; only http and https allowed")
    
    hostname = parsed.hostname
    if not hostname:
        raise SSRFError("missing hostname in target URL")
    
    if hostname.lower() == "localhost":
        raise SSRFError("access to 'localhost' is blocked")

    try:
        ip = ipaddress.ip_address(hostname)
        if is_ip_blocked(ip):
            raise SSRFError(f"target IP '{ip}' is in a restricted private/link-local address space")
        return url
    except ValueError:
        pass

    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for res in addr_info:
            ip_str = res[4][0]
            ip = ipaddress.ip_address(ip_str)
            if is_ip_blocked(ip):
                raise SSRFError(
                    f"domain '{hostname}' resolves to restricted IP '{ip_str}'"
                )
    except socket.gaierror:
        pass

    return url


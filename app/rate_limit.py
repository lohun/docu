import ipaddress

from slowapi import Limiter
from slowapi.util import get_remote_address


def _client_ip(request) -> str:
    """Resolve the effective client IP without trusting spoofed headers.

    The API sits behind an Nginx reverse proxy. The first X-Forwarded-For entry
    is only honored when the direct peer is a loopback/private address (i.e. the
    proxy itself); otherwise the direct peer is used so public clients can never
    self-spoof a fresh rate-limit bucket.
    """
    client = request.client
    peer = client.host if client is not None else None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and peer:
        try:
            peer_ip = ipaddress.ip_address(peer.split(",")[0].strip())
            if peer_ip.is_private or peer_ip.is_loopback:
                return forwarded.split(",")[0].strip()
        except ValueError:
            pass
    return peer or "unknown"


limiter = Limiter(key_func=_client_ip, default_limits=[])
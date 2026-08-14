from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


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


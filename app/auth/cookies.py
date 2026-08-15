"""Auth cookie names/attributes.

Names stay stable across environments: cookies are host-only (no Domain
attribute) so the ``__Host-`` hardening prefix adds no real protection and its
environment-dependent name would silently orphan sessions whenever the
environment setting changes. Hardening comes from the Secure/SameSite/HttpOnly
flags, which are derived from settings in the router cookie helpers.
"""

# `__Host-` prefix deliberately not used; see module docstring.
ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"


def access_cookie_name() -> str:
    return ACCESS_COOKIE


def refresh_cookie_name() -> str:
    return REFRESH_COOKIE
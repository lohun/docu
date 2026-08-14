from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def with_sslmode(url: str, sslmode: str) -> str:
    """Append an libpq-style ``sslmode`` query param to a DB URL.

    asyncpg and psycopg2 both honour ``sslmode`` from the URL query string,
    so a single URL can be shared across the async engine and the sync
    APScheduler jobstore. An empty ``sslmode`` leaves the URL untouched.
    """
    if not sslmode:
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["sslmode"] = sslmode
    rejoined = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(sorted(query.items())), parts.fragment)
    )
    return rejoined


def async_to_sync(url: str) -> str:
    """Convert an asyncpg (asyncio) URL to the equivalent psycopg2 (sync) URL.

    Only the driver scheme changes; host, port, credentials, database and any
    query params such as ``sslmode`` are preserved verbatim.
    """
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql+psycopg2://"):
        return url
    raise ValueError(f"unsupported database URL scheme: {url.split('://', 1)[0]}")
import hashlib
from dataclasses import dataclass
from typing import Protocol

from app.models.source import Source


@dataclass(frozen=True)
class AdapterResult:
    normalized: str
    raw_bytes: bytes
    excerpt: str
    screenshot: bytes | None = None


def compute_content_hash(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _make_excerpt(content: str, max_len: int = 2048) -> str:
    if len(content) <= max_len:
        return content
    return content[:max_len]


class SourceAdapter(Protocol):
    async def fetch(self, source: Source) -> AdapterResult: ...


def get_adapter(source_type: str) -> SourceAdapter:
    if source_type == "openapi":
        from app.adapters.openapi import OpenAPIAdapter

        return OpenAPIAdapter()
    if source_type == "scrape":
        from app.adapters.scrape import ScrapeAdapter

        return ScrapeAdapter()
    if source_type == "webapp":
        from app.adapters.webapp import WebappAdapter

        return WebappAdapter()
    raise ValueError(f"unknown source type: {source_type}")

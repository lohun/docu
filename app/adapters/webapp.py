import json
import logging

import httpx

from app.adapters.base import AdapterResult, _make_excerpt
from app.adapters.scrape import ScrapeAdapter
from app.models.source import Source
from app.security import validate_target_url

logger = logging.getLogger(__name__)


def extract_manifest_fingerprint(manifest: dict) -> str:
    """Build a stable fingerprint string from a build manifest.

    For Vite-style manifests this is the set of hashed entry filenames; the
    fingerprint changes only when the app actually rebuilds.
    """
    entries = []
    for key, entry in manifest.items():
        if isinstance(entry, dict) and entry.get("file"):
            entries.append(f"{key} {entry['file']}")
    if not entries:
        return json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return "\n".join(sorted(entries))


class WebappAdapter:
    """Fetch build manifest fingerprint when available; fall back to scrape."""

    def __init__(
        self,
        scrape_adapter: ScrapeAdapter | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.scrape_adapter = scrape_adapter or ScrapeAdapter()
        self._client = client

    async def fetch(self, source: Source) -> AdapterResult:
        manifest_url = f"{source.target_url.rstrip('/')}/manifest.json"
        try:
            validated_url = validate_target_url(manifest_url)
            if self._client is not None:
                resp = await self._client.get(validated_url)
            else:
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    resp = await client.get(validated_url)
            if resp.status_code == 200:
                    manifest = resp.json()
                    fingerprint = extract_manifest_fingerprint(manifest)
                    raw_bytes = fingerprint.encode("utf-8")
                    return AdapterResult(
                        normalized=fingerprint,
                        raw_bytes=raw_bytes,
                        excerpt=_make_excerpt(fingerprint),
                    )
        except Exception:
            logger.info(
                "manifest fetch failed for %s; falling back to scrape adapter",
                source.target_url,
            )
        return await self.scrape_adapter.fetch(source)

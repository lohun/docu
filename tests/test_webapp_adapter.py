import httpx
import pytest

from app.adapters.base import AdapterResult
from app.adapters.webapp import WebappAdapter, extract_manifest_fingerprint
from app.models.source import Source

MANIFEST = {
    "src/main.ts": {"file": "assets/main-a1b2c3.js", "isEntry": True},
    "src/api.ts": {"file": "assets/api-d4e5f6.js", "isEntry": False},
}


def _manifest_client(status: int = 200) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        if status == 200:
            return httpx.Response(200, json=MANIFEST)
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _source() -> Source:
    return Source(
        org_id=1,
        name="Webapp",
        type="webapp",
        target_url="https://example.com/app",
        is_active=True,
    )


@pytest.mark.anyio
async def test_webapp_uses_manifest_fingerprint_when_available() -> None:
    adapter = WebappAdapter(client=_manifest_client())
    result = await adapter.fetch(_source())

    assert "assets/main-a1b2c3.js" in result.normalized
    assert "assets/api-d4e5f6.js" in result.normalized
    assert result.raw_bytes == result.normalized.encode("utf-8")


@pytest.mark.anyio
async def test_webapp_falls_back_to_scrape_adapter() -> None:
    class _FallbackScrapeAdapter:
        async def fetch(self, source: Source) -> AdapterResult:
            return AdapterResult(
                normalized="scraped content",
                raw_bytes=b"scraped content",
                excerpt="scraped content",
            )

    adapter = WebappAdapter(
        scrape_adapter=_FallbackScrapeAdapter(),  # type: ignore[arg-type]
        client=_manifest_client(status=404),
    )
    result = await adapter.fetch(_source())
    assert result.normalized == "scraped content"


def test_manifest_fingerprint_sorts_entries() -> None:
    fingerprint = extract_manifest_fingerprint(MANIFEST)
    lines = fingerprint.splitlines()
    assert lines == sorted(lines)
    assert len(lines) == 2


def test_manifest_fingerprint_falls_back_to_canonical_json() -> None:
    fingerprint = extract_manifest_fingerprint({"build_id": "abc"})
    assert fingerprint == '{"build_id":"abc"}'

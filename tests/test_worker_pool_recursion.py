import httpx
import pytest

from app.scraper.worker_pool import (
    ScraperWorkerPool,
    _extract_links_from_html,
    _is_internal_link,
    _normalize_link,
)


def test_is_internal_link() -> None:
    base = "https://example.com/docs/api"
    assert _is_internal_link("https://example.com/docs/page2", base) is True
    assert _is_internal_link("https://example.com/other", base) is True
    assert _is_internal_link("https://sub.example.com/other", base) is False
    assert _is_internal_link("https://google.com", base) is False
    assert _is_internal_link("javascript:void(0)", base) is False


def test_normalize_link() -> None:
    base = "https://example.com/docs/index.html"
    assert _normalize_link("/docs/page2.html#section1", base) == "https://example.com/docs/page2.html"
    assert _normalize_link("page3.html", base) == "https://example.com/docs/page3.html"
    assert _normalize_link("https://example.com/file.pdf", base) is None
    assert _normalize_link("mailto:admin@example.com", base) is None
    assert _normalize_link("javascript:alert(1)", base) is None


def test_extract_links_from_html() -> None:
    html = """
    <html>
      <body>
        <a href="/page1">Page 1</a>
        <a href="https://example.com/page2#foo">Page 2</a>
        <a href="https://external.com">External</a>
        <a href="/doc.pdf">PDF</a>
      </body>
    </html>
    """
    links = _extract_links_from_html(html, "https://example.com")
    assert "https://example.com/page1" in links
    assert "https://example.com/page2" in links
    assert "https://external.com" in links
    assert "https://example.com/doc.pdf" not in links


@pytest.mark.anyio
async def test_recursive_httpx_scrape_multi_page(monkeypatch) -> None:
    monkeypatch.setattr("playwright.async_api.async_playwright", None)

    mock_responses = {
        "https://example.org/docs": """
        <html>
          <body>
            <div id="content">
              <h1>Main Doc</h1>
              <a href="/docs/page2">Page 2</a>
              <a href="https://external.com">External</a>
            </div>
          </body>
        </html>
        """,
        "https://example.org/docs/page2": """
        <html>
          <body>
            <div id="content">
              <h2>Page 2 Content</h2>
              <a href="/docs">Main Doc</a>
            </div>
          </body>
        </html>
        """,
    }

    async def mock_get(self, url, **kwargs):
        url_str = str(url)
        req = httpx.Request("GET", url_str)
        if url_str in mock_responses:
            return httpx.Response(200, text=mock_responses[url_str], request=req)
        return httpx.Response(404, text="Not Found", request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    pool = ScraperWorkerPool()
    content, content_hash, screenshot = await pool.scrape_url(
        "https://example.org/docs",
        css_scope_selector="#content",
        capture_screenshot=False,
        max_pages=10,
    )

    assert "--- Page: https://example.org/docs ---" in content
    assert "Main Doc" in content
    assert "--- Page: https://example.org/docs/page2 ---" in content
    assert "Page 2 Content" in content
    assert "external.com" not in content
    assert len(content_hash) == 64


@pytest.mark.anyio
async def test_recursive_scrape_max_pages_cap(monkeypatch) -> None:
    monkeypatch.setattr("playwright.async_api.async_playwright", None)

    mock_responses = {}
    for i in range(1, 6):
        next_link = f'<a href="/p{i+1}">Next</a>' if i < 5 else ""
        mock_responses[f"https://example.org/p{i}"] = f"<html><body><h1>Page {i}</h1>{next_link}</body></html>"

    async def mock_get(self, url, **kwargs):
        url_str = str(url)
        req = httpx.Request("GET", url_str)
        if url_str in mock_responses:
            return httpx.Response(200, text=mock_responses[url_str], request=req)
        return httpx.Response(404, text="Not Found", request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    pool = ScraperWorkerPool()
    content, content_hash, _ = await pool.scrape_url(
        "https://example.org/p1",
        max_pages=2,
    )

    assert "--- Page: https://example.org/p1 ---" in content
    assert "--- Page: https://example.org/p2 ---" in content
    assert "https://example.org/p3" not in content


@pytest.mark.anyio
async def test_recursive_scrape_skips_ssrf_internal_links(monkeypatch) -> None:
    monkeypatch.setattr("playwright.async_api.async_playwright", None)

    fetched_urls = []

    mock_responses = {
        "https://example.org/start": """
        <html>
          <body>
            <a href="http://127.0.0.1/admin">Secret Admin Link</a>
            <a href="https://example.org/safe">Safe Page</a>
          </body>
        </html>
        """,
        "https://example.org/safe": "<html><body><h1>Safe Content</h1></body></html>",
        "http://127.0.0.1/admin": "<html><body><h1>SENSITIVE_LOCAL_DATA</h1></body></html>",
    }

    async def mock_get(self, url, **kwargs):
        url_str = str(url)
        fetched_urls.append(url_str)
        req = httpx.Request("GET", url_str)
        if url_str in mock_responses:
            return httpx.Response(200, text=mock_responses[url_str], request=req)
        return httpx.Response(404, text="Not Found", request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    pool = ScraperWorkerPool()
    content, _, _ = await pool.scrape_url("https://example.org/start")

    assert "Safe Content" in content
    assert "SENSITIVE_LOCAL_DATA" not in content
    assert "http://127.0.0.1/admin" not in fetched_urls

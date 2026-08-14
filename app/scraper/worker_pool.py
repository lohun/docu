import asyncio
import hashlib
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.security import SSRFError, validate_target_url

_IGNORE_EXTENSIONS = (
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js",
    ".zip", ".tar", ".gz", ".mp4", ".mp3", ".avi", ".mov", ".woff", ".woff2"
)


def _is_internal_link(target_url: str, base_url: str) -> bool:
    target_parsed = urlparse(target_url)
    base_parsed = urlparse(base_url)
    if target_parsed.scheme not in ("http", "https"):
        return False
    return target_parsed.netloc.lower() == base_parsed.netloc.lower()


def _normalize_link(raw_href: str, current_page_url: str) -> str | None:
    if not raw_href:
        return None
    raw_href = raw_href.strip()
    if raw_href.startswith(("mailto:", "tel:", "javascript:", "data:", "ftp:")):
        return None

    joined = urljoin(current_page_url, raw_href)
    parsed = urlparse(joined)
    if parsed.scheme not in ("http", "https"):
        return None

    # Strip fragment
    normalized = parsed._replace(fragment="").geturl()

    # Check extension
    path_lower = parsed.path.lower()
    if any(path_lower.endswith(ext) for ext in _IGNORE_EXTENSIONS):
        return None

    return normalized


def _extract_links_from_html(html: str, current_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        normalized = _normalize_link(href, current_url)
        if normalized:
            links.append(normalized)
    return links


class ScraperWorkerPool:
    def __init__(self, max_concurrency: int = 3) -> None:
        self.max_concurrency = max(1, min(max_concurrency, 5))
        self._semaphore = asyncio.Semaphore(self.max_concurrency)

    @asynccontextmanager
    async def acquire(self) -> AsyncGenerator[None, None]:
        async with self._semaphore:
            yield

    async def scrape_url(
        self,
        url: str,
        css_scope_selector: str | None = None,
        capture_screenshot: bool = False,
        max_pages: int = 25,
    ) -> tuple[str, str, bytes | None]:
        # 1. SSRF Target Validation
        validated_url = validate_target_url(url)

        async with self.acquire():
            screenshot: bytes | None = None
            scraped_pages: list[tuple[str, str]] = []
            visited_urls: set[str] = {validated_url}
            to_visit: list[str] = [validated_url]

            # Attempt Playwright scraping first if available, otherwise fallback to httpx
            try:
                from playwright.async_api import async_playwright
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    page = await browser.new_page()

                    while to_visit and len(scraped_pages) < max_pages:
                        current_url = to_visit.pop(0)

                        try:
                            valid_current = validate_target_url(current_url)
                        except SSRFError:
                            continue

                        try:
                            await page.goto(valid_current, timeout=30000, wait_until="domcontentloaded")
                        except Exception:
                            continue

                        if css_scope_selector:
                            element = await page.query_selector(css_scope_selector)
                            if element:
                                content = await element.inner_text()
                            else:
                                content = await page.content()
                        else:
                            content = await page.content()

                        scraped_pages.append((valid_current, content))

                        if valid_current == validated_url and capture_screenshot:
                            try:
                                screenshot = await page.screenshot(full_page=True)
                            except Exception:
                                pass

                        # Discover internal links
                        try:
                            raw_links = await page.eval_on_selector_all(
                                "a[href]", "els => els.map(e => e.getAttribute('href'))"
                            )
                        except Exception:
                            raw_links = []

                        for href in raw_links:
                            normalized = _normalize_link(href, valid_current)
                            if (
                                normalized
                                and normalized not in visited_urls
                                and _is_internal_link(normalized, validated_url)
                                and not None
                            ):
                                visited_urls.add(normalized)
                                to_visit.append(normalized)

                    await browser.close()
            except Exception as e:
                # Fallback to HTTP client fetch if Playwright fails/not installed
                import httpx
                scraped_pages.clear()
                visited_urls = {validated_url}
                to_visit = [validated_url]

                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    while to_visit and len(scraped_pages) < max_pages:
                        current_url = to_visit.pop(0)

                        try:
                            valid_current = validate_target_url(current_url)
                        except SSRFError:
                            continue

                        try:
                            if client is None:
                                raw_html = ""
                            else:
                                resp = await client.get(valid_current)
                                resp.raise_for_status()
                                raw_html = resp.text
                        except Exception:
                            continue

                        if css_scope_selector:
                            soup = BeautifulSoup(raw_html, "html.parser")
                            scoped = soup.select_one(css_scope_selector)
                            content = scoped.get_text("\n") if scoped else raw_html
                        else:
                            content = raw_html

                        scraped_pages.append((valid_current, content))

                        # Discover internal links
                        discovered_links = _extract_links_from_html(raw_html, valid_current)
                        for link in discovered_links:
                            if link not in visited_urls and _is_internal_link(link, validated_url):
                                visited_urls.add(link)
                                to_visit.append(link)

            # Option A: Format multi-page output with explicit headers
            if not scraped_pages:
                full_content = ""
            elif len(scraped_pages) == 1:
                full_content = scraped_pages[0][1]
            else:
                formatted_parts = []
                for p_url, p_content in scraped_pages:
                    formatted_parts.append(f"--- Page: {p_url} ---\n{p_content}")
                full_content = "\n\n".join(formatted_parts)

            content_hash = hashlib.sha256(full_content.encode("utf-8")).hexdigest()
            return full_content, content_hash, screenshot

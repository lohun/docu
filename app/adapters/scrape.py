import re

from bs4 import BeautifulSoup

from app.adapters.base import AdapterResult, _make_excerpt
from app.diff.text_diff import normalize_timestamps
from app.models.source import Source
from app.scraper.worker_pool import ScraperWorkerPool

_AD_ANALYTICS_CLASS_RE = re.compile(
    r"^(ad-|ads|banner|promo|analytics|tracking|cookie|cmp-|sponsored)", re.I
)


def _normalize_single_html(html: str, css_scope_selector: str | None = None) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "iframe", "template", "svg", "canvas"]):
        tag.decompose()

    for tag in list(soup.find_all(True)):
        if tag.attrs is None:
            continue
        class_names = tag.get("class") or []
        if any(_AD_ANALYTICS_CLASS_RE.match(name) for name in class_names if name):
            tag.decompose()

    if css_scope_selector:
        scoped = soup.select_one(css_scope_selector)
        if scoped is not None:
            soup = BeautifulSoup(str(scoped), "html.parser")

    for tag in soup.find_all(True):
        if tag.attrs is None:
            continue
        for attr in list(tag.attrs):
            if attr.startswith("data-") or attr.startswith("ng-") or attr in ("aria-atomic", "aria-live"):
                del tag.attrs[attr]

    text = soup.get_text("\n")
    text = normalize_timestamps(text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def normalize_html(html: str, css_scope_selector: str | None = None) -> str:
    """Reduce HTML or multi-page text to stable, diffable text.

    Removes script/style/analytics noise and dynamic timestamps so cosmetic
    page churn does not change the content hash.
    Preserves '--- Page: <URL> ---' section headers if multi-page content is passed.
    """
    if "--- Page: " in html:
        parts = html.split("--- Page: ")
        normalized_sections = []
        for part in parts:
            if not part.strip():
                continue
            lines = part.splitlines()
            header_line = lines[0]  # e.g. "https://example.com/page ---"
            body = "\n".join(lines[1:])
            norm_body = _normalize_single_html(body, css_scope_selector)
            normalized_sections.append(f"--- Page: {header_line}\n{norm_body}")
        return "\n\n".join(normalized_sections)
    return _normalize_single_html(html, css_scope_selector)


class ScrapeAdapter:
    def __init__(self, worker_pool: ScraperWorkerPool | None = None) -> None:
        self.worker_pool = worker_pool or ScraperWorkerPool(max_concurrency=3)

    async def fetch(self, source: Source) -> AdapterResult:
        content, _, screenshot = await self.worker_pool.scrape_url(
            source.target_url,
            source.css_scope_selector,
            capture_screenshot=True,
        )
        normalized = normalize_html(content, source.css_scope_selector)
        raw_bytes = normalized.encode("utf-8")
        return AdapterResult(
            normalized=normalized,
            raw_bytes=raw_bytes,
            excerpt=_make_excerpt(normalized),
            screenshot=screenshot,
        )

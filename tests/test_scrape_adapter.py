import pytest

from app.adapters.base import compute_content_hash
from app.adapters.scrape import normalize_html

HTML = """\
<!doctype html>
<html>
<head><title>Docs</title><style>body { color: red; }</style></head>
<body>
  <div id="page">
    <script>window.track();</script>
    <nav class="ad-banner">Buy now!</nav>
    <main id="api-reference">
      <h1>Reference</h1>
      <p>Last updated 2026-08-01T12:00:00Z</p>
      <p data-session-id="abc123">Stable text</p>
    </main>
  </div>
</body>
</html>
"""


def test_scrape_strips_script_tags_and_timestamps() -> None:
    text = normalize_html(HTML)
    assert "window.track" not in text
    assert "color: red" not in text
    assert "Buy now!" not in text
    assert "2026-08-01" not in text
    assert "<timestamp>" in text
    assert "Stable text" in text


def test_scrape_respects_css_scope_selector() -> None:
    text = normalize_html(HTML, css_scope_selector="#api-reference")
    assert "Reference" in text
    assert "Stable text" in text
    assert "Buy now!" not in text
    assert "data-session-id" not in text


def test_normalized_content_hash_is_stable_after_cosmetic_html_changes() -> None:
    original = normalize_html(HTML)
    changed_timestamp = normalize_html(
        HTML.replace("2026-08-01T12:00:00Z", "2026-08-02T13:30:00Z")
    )
    changed_session = normalize_html(
        HTML.replace('data-session-id="abc123"', 'data-session-id="xyz789"')
    )

    assert compute_content_hash(original) == compute_content_hash(changed_timestamp)
    assert compute_content_hash(original) == compute_content_hash(changed_session)


def test_scrape_scope_selector_missing_falls_back_to_full_page() -> None:
    text = normalize_html(HTML, css_scope_selector="#does-not-exist")
    assert "Stable text" in text


def test_normalize_survives_tag_with_none_attrs(monkeypatch) -> None:
    """Regression: decompose() clears Tag.__dict__ (attrs -> None), and such a
    node can surface in find_all(True). Iterating it with tag.get('class')
    crashed with 'NoneType' object has no attribute 'get' (devx.today scrape).
    """
    from bs4 import BeautifulSoup

    orig_find_all = BeautifulSoup.find_all

    def patched_find_all(self, *args, **kwargs):
        result = list(orig_find_all(self, *args, **kwargs))
        victim = self.new_tag("div")
        victim.decompose()
        assert victim.attrs is None
        result.append(victim)
        return result

    monkeypatch.setattr("bs4.BeautifulSoup.find_all", patched_find_all)

    text = normalize_html("<div><p>Stable text</p></div>")
    assert "Stable text" in text

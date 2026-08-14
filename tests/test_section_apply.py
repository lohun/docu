import pytest

from app.publish.section_apply import (
    FullDocumentReplacementError,
    apply_section_update,
    extract_section,
)

DOC = """# Pet Store API

## Introduction

This is the intro.

## Authentication

Use a bearer token.

## Endpoints

Base path is `/v1`.

### List Pets

Returns a list.

## Rate Limits

100 req/min.
"""


def test_section_apply_replaces_only_target_section() -> None:
    updated = apply_section_update(DOC, "Authentication", "Use an API key header instead.")

    assert "Use an API key header instead." in updated
    assert "Use a bearer token." not in updated
    assert "This is the intro." in updated
    assert "Base path is `/v1`." in updated
    assert "Returns a list." in updated


def test_section_apply_preserves_sibling_sections() -> None:
    updated = apply_section_update(DOC, "Endpoints", "Base path is `/v2`.")
    assert "Base path is `/v2`." in updated
    assert "Rate Limits" in updated
    assert "100 req/min." in updated


def test_section_apply_appends_new_section() -> None:
    updated = apply_section_update(DOC, "Webhooks", "New webhook section body.")
    assert "## Webhooks" in updated
    assert "New webhook section body." in updated
    assert "100 req/min." in updated


def test_section_apply_rejects_full_document_replacement() -> None:
    whole_doc = "\n".join(
        [
            "## Introduction\n\nIntro body.",
            "## Authentication\n\nAuth body.",
            "## Endpoints\n\nEndpoints body.",
        ]
    )
    with pytest.raises(FullDocumentReplacementError):
        apply_section_update(DOC, "Introduction", whole_doc)


def test_extract_section_returns_body_only() -> None:
    body = extract_section(DOC, "Authentication")
    assert body == "Use a bearer token."


def test_extract_section_missing_returns_none() -> None:
    assert extract_section(DOC, "Missing Section") is None

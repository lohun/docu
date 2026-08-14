import hashlib

import httpx
import pytest

from app.adapters.base import compute_content_hash
from app.adapters.openapi import OpenAPIAdapter, canonicalize_openapi
from app.models.source import Source


def _make_source(url: str = "https://example.com/openapi.json") -> Source:
    return Source(
        org_id=1,
        name="Spec",
        type="openapi",
        target_url=url,
        is_active=True,
    )


def _client_for(body: str, content_type: str = "application/json") -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": content_type})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


SPEC_JSON = """{
  "paths": {"\u002Fpets": {"get": {"operationId": "listPets"}}},
  "info": {"title": "Pet Store", "version": "1.0.0"},
  "openapi": "3.0.0"
}"""

SPEC_JSON_SHIFTED_KEYS = """{
  "openapi": "3.0.0",
  "info": {"version": "1.0.0", "title": "Pet Store"},
  "paths": {"\u002Fpets": {"get": {"operationId": "listPets"}}}
}"""

SPEC_YAML = """\
openapi: 3.0.0
info:
  title: Pet Store
  version: 1.0.0
paths:
  /pets:
    get:
      operationId: listPets
"""


@pytest.mark.anyio
async def test_openapi_fetch_and_canonicalize_json_sorts_keys() -> None:
    adapter = OpenAPIAdapter(client=_client_for(SPEC_JSON))
    result = await adapter.fetch(_make_source())

    assert result.normalized == (
        '{"info":{"title":"Pet Store","version":"1.0.0"},'
        '"openapi":"3.0.0","paths":{"/pets":{"get":{"operationId":"listPets"}}}}'
    )
    assert result.raw_bytes == result.normalized.encode("utf-8")
    assert result.excerpt.startswith('{"info":')


@pytest.mark.anyio
async def test_openapi_fetch_parses_yaml_to_json() -> None:
    adapter = OpenAPIAdapter(
        client=_client_for(SPEC_YAML, content_type="application/yaml")
    )
    result = await adapter.fetch(_make_source())

    assert result.normalized == (
        '{"info":{"title":"Pet Store","version":"1.0.0"},'
        '"openapi":"3.0.0","paths":{"/pets":{"get":{"operationId":"listPets"}}}}'
    )


def test_openapi_hash_is_stable_across_key_order() -> None:
    canonical_a = canonicalize_openapi(SPEC_JSON)
    canonical_b = canonicalize_openapi(SPEC_JSON_SHIFTED_KEYS)

    assert canonical_a == canonical_b
    assert compute_content_hash(canonical_a) == compute_content_hash(canonical_b)


def test_openapi_canonicalize_rejects_non_object_root() -> None:
    with pytest.raises(ValueError, match="root must be a JSON object"):
        canonicalize_openapi("[1, 2, 3]")


def test_openapi_canonicalize_rejects_invalid_spec() -> None:
    with pytest.raises(ValueError, match="unable to parse"):
        canonicalize_openapi('{"unclosed": true')

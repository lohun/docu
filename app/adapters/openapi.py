import json

import httpx
import yaml

from app.adapters.base import AdapterResult, _make_excerpt
from app.models.source import Source
from app.security import validate_target_url


def canonicalize_openapi(content: str, content_type: str = "") -> str:
    """Normalize an OpenAPI spec to canonical JSON (sorted keys, compact).

    Key order is discarded so hashing and diffing are stable regardless of the
    formatting the author used (JSON or YAML, pretty or minified).
    """
    text = content.strip()
    is_json = "json" in content_type.lower() or text.startswith("{")
    try:
        if is_json:
            obj = json.loads(text)
        else:
            obj = yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise ValueError(f"unable to parse OpenAPI spec: {e}") from e
    if not isinstance(obj, dict):
        raise ValueError("OpenAPI spec root must be a JSON object")
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


class OpenAPIAdapter:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def fetch(self, source: Source) -> AdapterResult:
        validated_url = validate_target_url(source.target_url)
        if self._client is not None:
            resp = await self._client.get(validated_url)
        else:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(validated_url)
        resp.raise_for_status()
        canonical = canonicalize_openapi(resp.text, resp.headers.get("content-type", ""))
        raw_bytes = canonical.encode("utf-8")
        return AdapterResult(
            normalized=canonical,
            raw_bytes=raw_bytes,
            excerpt=_make_excerpt(canonical),
        )

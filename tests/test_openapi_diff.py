import pytest

from app.diff.openapi_diff import OpenAPIDiffEngine, compute_openapi_diff

SPEC_V1 = {
    "openapi": "3.0.0",
    "info": {"title": "Pet Store", "version": "1.0.0"},
    "paths": {
        "/pets": {
            "get": {"operationId": "listPets", "description": "List all pets"}
        }
    },
    "components": {"schemas": {"Pet": {"type": "object", "properties": {"name": {"type": "string"}}}}},
}

SPEC_V2 = {
    "openapi": "3.0.0",
    "info": {"title": "Pet Store", "version": "1.1.0"},
    "paths": {
        "/pets": {
            "get": {"operationId": "listPets", "description": "List all pets"},
            "post": {"operationId": "createPet"},
        }
    },
    "components": {"schemas": {"Pet": {"type": "object", "properties": {"name": {"type": "string"}}}}},
}


def _dump(obj: dict) -> bytes:
    import json

    return json.dumps(obj, sort_keys=True).encode("utf-8")


def test_oasdiff_produces_structured_changelog_in_diff_payload() -> None:
    payload = compute_openapi_diff(_dump(SPEC_V1), _dump(SPEC_V2))

    assert payload["format"] == "oasdiff"
    assert payload["change_count"] > 0
    assert isinstance(payload["changes"], list)

    types = {change["type"] for change in payload["changes"]}
    assert "added" in types
    assert "changed" in types
    assert any(
        change["path"] == "/paths//pets/post" for change in payload["changes"]
    )
    assert any(
        change["path"] == "/info/version" and change["type"] == "changed"
        for change in payload["changes"]
    )


def test_no_changes_returns_empty_changelog() -> None:
    payload = compute_openapi_diff(_dump(SPEC_V1), _dump(SPEC_V1))
    assert payload["change_count"] == 0
    assert payload["changes"] == []


def test_openapi_diff_type_is_oasdiff() -> None:
    assert OpenAPIDiffEngine.diff_type == "oasdiff"


def test_removed_operation_is_detected() -> None:
    payload = compute_openapi_diff(_dump(SPEC_V2), _dump(SPEC_V1))
    assert any(
        change["type"] == "removed" and change["path"] == "/paths//pets/post"
        for change in payload["changes"]
    )

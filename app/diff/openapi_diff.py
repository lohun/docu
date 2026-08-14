import json

from app.diff.base import DiffResult


def _truncate(value, max_len: int = 2000) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, default=str)
    if len(value) <= max_len:
        return value
    return value[:max_len] + "...<truncated>"


def _diff_json(from_obj: object, to_obj: object, path: str = "") -> list[dict]:
    changes: list[dict] = []
    if isinstance(from_obj, dict) and isinstance(to_obj, dict):
        for key in sorted(set(from_obj) | set(to_obj)):
            child_path = f"{path}/{key}" if path else f"/{key}"
            if key not in from_obj:
                changes.append(
                    {"type": "added", "path": child_path, "value": _truncate(to_obj[key])}
                )
            elif key not in to_obj:
                changes.append(
                    {"type": "removed", "path": child_path, "value": _truncate(from_obj[key])}
                )
            else:
                changes.extend(_diff_json(from_obj[key], to_obj[key], child_path))
    elif isinstance(from_obj, list) and isinstance(to_obj, list):
        for index, (old_item, new_item) in enumerate(zip(from_obj, to_obj)):
            changes.extend(_diff_json(old_item, new_item, f"{path}/{index}"))
        if len(from_obj) < len(to_obj):
            for index in range(len(from_obj), len(to_obj)):
                changes.append(
                    {
                        "type": "added",
                        "path": f"{path}/{index}",
                        "value": _truncate(to_obj[index]),
                    }
                )
        elif len(from_obj) > len(to_obj):
            for index in range(len(to_obj), len(from_obj)):
                changes.append(
                    {
                        "type": "removed",
                        "path": f"{path}/{index}",
                        "value": _truncate(from_obj[index]),
                    }
                )
    elif from_obj != to_obj:
        changes.append(
            {
                "type": "changed",
                "path": path,
                "old_value": _truncate(from_obj),
                "new_value": _truncate(to_obj),
            }
        )
    return changes


def compute_openapi_diff(from_raw: bytes, to_raw: bytes) -> dict:
    """Produce an oasdiff-style structured changelog between two canonical specs.

    Both inputs are expected to be canonical JSON (see OpenAPIAdapter); key
    ordering is therefore already stable and differences are meaningful.
    """
    from_obj = json.loads(from_raw.decode("utf-8"))
    to_obj = json.loads(to_raw.decode("utf-8"))
    changes = _diff_json(from_obj, to_obj)
    return {"format": "oasdiff", "change_count": len(changes), "changes": changes}


class OpenAPIDiffEngine:
    diff_type = "oasdiff"

    async def compute(self, store, from_snapshot, to_snapshot) -> DiffResult:
        from_raw = store.read_raw(from_snapshot.raw_storage_ref)
        to_raw = store.read_raw(to_snapshot.raw_storage_ref)
        payload = compute_openapi_diff(from_raw, to_raw)
        return DiffResult(payload=payload, is_trivial=False, diff_type=self.diff_type)

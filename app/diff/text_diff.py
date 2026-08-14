import difflib
import re

from app.diff.base import DiffResult

# Below this change ratio a diff is considered cosmetic and the LLM is skipped.
TRIVIAL_RATIO_THRESHOLD = 0.02

_ISO_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"
)


def _only_whitespace_differences(from_lines: list[str], to_lines: list[str]) -> bool:
    compact_from = [re.sub(r"\s+", "", line) for line in from_lines]
    compact_to = [re.sub(r"\s+", "", line) for line in to_lines]
    return compact_from == compact_to


def _changed_line_count(from_lines: list[str], to_lines: list[str]) -> int:
    matcher = difflib.SequenceMatcher(None, from_lines, to_lines)
    changed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete", "insert"):
            changed += max(i2 - i1, j2 - j1)
    return changed


def compute_text_diff(from_text: str, to_text: str) -> tuple[dict, bool]:
    from_lines = from_text.splitlines()
    to_lines = to_text.splitlines()

    diff_lines = list(difflib.unified_diff(from_lines, to_lines, lineterm=""))
    changed = _changed_line_count(from_lines, to_lines)
    base = max(len(from_lines), len(to_lines), 1)
    change_ratio = changed / base

    is_trivial = change_ratio < TRIVIAL_RATIO_THRESHOLD
    if _only_whitespace_differences(from_lines, to_lines):
        is_trivial = True

    payload = {
        "format": "unified",
        "lines": diff_lines,
        "change_ratio": round(change_ratio, 4),
    }
    return payload, is_trivial


def normalize_timestamps(text: str) -> str:
    """Replace dynamic ISO timestamps so cosmetic page churn doesn't change hashes."""
    return _ISO_TIMESTAMP_RE.sub("<timestamp>", text)


class TextDiffEngine:
    diff_type = "text"

    async def compute(self, store, from_snapshot, to_snapshot) -> DiffResult:
        from_text = store.read_raw(from_snapshot.raw_storage_ref).decode(
            "utf-8", errors="replace"
        )
        to_text = store.read_raw(to_snapshot.raw_storage_ref).decode(
            "utf-8", errors="replace"
        )
        payload, is_trivial = compute_text_diff(from_text, to_text)
        return DiffResult(payload=payload, is_trivial=is_trivial, diff_type=self.diff_type)

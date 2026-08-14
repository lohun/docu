import pytest

from app.diff.text_diff import TRIVIAL_RATIO_THRESHOLD, TextDiffEngine, compute_text_diff

BASE = "\n".join(f"line {i}" for i in range(100))
CHANGED_ONE_LINE = "\n".join(f"line {i}" if i != 50 else "line 50 changed" for i in range(100))
WHITESPACE_ONLY = "\n".join(f"  line {i}  " for i in range(100))
HEAVILY_CHANGED = "\n".join(f"totally different content {i}" for i in range(100))


def test_text_diff_produces_unified_diff_payload() -> None:
    payload, is_trivial = compute_text_diff(BASE, CHANGED_ONE_LINE)

    assert payload["format"] == "unified"
    assert isinstance(payload["lines"], list)
    assert payload["lines"]
    assert "change_ratio" in payload
    assert any(line.startswith("-") or line.startswith("+") for line in payload["lines"])


def test_trivial_change_below_ratio_threshold_sets_is_trivial_true() -> None:
    _, is_trivial = compute_text_diff(BASE, CHANGED_ONE_LINE)
    assert 1 / 100 < TRIVIAL_RATIO_THRESHOLD
    assert is_trivial is True


def test_whitespace_only_change_is_trivial() -> None:
    _, is_trivial = compute_text_diff(BASE, WHITESPACE_ONLY)
    assert is_trivial is True


def test_substantive_content_change_is_not_trivial() -> None:
    _, is_trivial = compute_text_diff(BASE, HEAVILY_CHANGED)
    assert is_trivial is False


def test_identical_content_is_trivial() -> None:
    payload, is_trivial = compute_text_diff(BASE, BASE)
    assert is_trivial is True
    assert payload["lines"] == []


def test_text_diff_engine_type_is_text() -> None:
    assert TextDiffEngine.diff_type == "text"

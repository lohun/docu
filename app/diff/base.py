from dataclasses import dataclass


@dataclass(frozen=True)
class DiffResult:
    payload: dict
    is_trivial: bool
    diff_type: str

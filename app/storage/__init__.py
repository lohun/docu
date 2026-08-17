from typing import Protocol

from app.config import get_settings


class SnapshotStore(Protocol):
    """Storage interface shared by the local-disk and Cloudinary backends."""

    def write_raw(self, snapshot_id: int, content: bytes, suffix: str = "raw") -> str: ...

    def read_raw(self, raw_storage_ref: str) -> bytes: ...

    def delete_raw(self, raw_storage_ref: str) -> None: ...

    def list_refs(self) -> list[str]: ...


def get_snapshot_store() -> SnapshotStore:
    """Return the configured snapshot blob store backend.

    - ``local`` (default, tests/dev): filesystem under ``snapshot_storage_dir``.
    - ``cloudinary`` (production): private-delivery assets under Cloudinary.

    Both implement the same interface (write_raw/read_raw/delete_raw/list_refs),
    keeping diff engines and the scheduler backend-agnostic.
    """
    settings = get_settings()
    if settings.storage_backend == "cloudinary":
        from app.storage.cloudinary_store import CloudinaryStore

        return CloudinaryStore(settings.cloudinary_url)
    from app.storage.snapshot_store import SnapshotStore as LocalDiskStore

    return LocalDiskStore(settings.snapshot_storage_dir)
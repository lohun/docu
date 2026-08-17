import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class SnapshotStore:
    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir).resolve()

    def write_raw(self, snapshot_id: int, content: bytes, suffix: str = "raw") -> str:
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            path = self.base_dir / f"{snapshot_id}.{suffix}"
            path.write_bytes(content)
            return str(path)
        except PermissionError as e:
            logger.error(f"Permission denied writing to {self.base_dir}: {e}")
            raise
        except OSError as e:
            logger.error(f"Failed to write snapshot {snapshot_id}: {e}")
            raise

    def read_raw(self, raw_storage_ref: str) -> bytes:
        try:
            return Path(raw_storage_ref).read_bytes()
        except FileNotFoundError:
            logger.error(f"Snapshot file not found: {raw_storage_ref}")
            raise
        except PermissionError as e:
            logger.error(f"Permission denied reading {raw_storage_ref}: {e}")
            raise

    def delete_raw(self, raw_storage_ref: str) -> None:
        path = Path(raw_storage_ref)
        if path.exists():
            try:
                path.unlink()
            except PermissionError as e:
                logger.error(f"Permission denied deleting {raw_storage_ref}: {e}")
                raise

    def list_refs(self) -> list[str]:
        """List every snapshot ref currently stored (for orphan cleanup)."""
        if not self.base_dir.exists():
            return []
        refs = []
        for path in self.base_dir.iterdir():
            if (
                path.is_file()
                and path.suffix in (".raw", ".png", ".jpg", ".jpeg", ".webp")
                and path.stem.isdigit()
            ):
                refs.append(str(path))
        return refs

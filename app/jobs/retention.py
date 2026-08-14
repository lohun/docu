from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.diff import Diff
from app.models.snapshot import Snapshot
from app.storage.snapshot_store import SnapshotStore

DEFAULT_RETENTION_DAYS = 90


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def cleanup_expired_data(
    session: AsyncSession,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    base_dir: str | Path | None = None,
) -> dict:
    """Delete snapshots/diffs older than the retention window and orphaned raw files.

    Docs are retained indefinitely — they are the authoritative materialized view.
    """
    cutoff = _now() - timedelta(days=retention_days)
    store = SnapshotStore(base_dir or get_settings().snapshot_storage_dir)

    expired = list(
        await session.scalars(
            select(Snapshot).where(Snapshot.fetched_at < cutoff)
        )
    )
    for snapshot in expired:
        try:
            store.delete_raw(snapshot.raw_storage_ref)
        except OSError:
            pass

    if expired:
        await session.execute(
            delete(Diff).where(Diff.created_at < cutoff)
        )
        for snapshot in expired:
            await session.delete(snapshot)
        await session.flush()

    orphaned_files = 0
    base_path = Path(store.base_dir)
    known_ids = set((await session.scalars(select(Snapshot.id))).all())
    if base_path.exists():
        for path in base_path.iterdir():
            if (
                path.is_file()
                and path.suffix in (".raw", ".png")
                and path.stem.isdigit()
                and int(path.stem) not in known_ids
            ):
                path.unlink()
                orphaned_files += 1

    await session.commit()
    return {
        "snapshots_deleted": len(expired),
        "orphaned_files_deleted": orphaned_files,
    }

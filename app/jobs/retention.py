from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.diff import Diff
from app.models.snapshot import Snapshot
from app.storage import get_snapshot_store
from app.storage.snapshot_store import SnapshotStore

DEFAULT_RETENTION_DAYS = 90


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def cleanup_expired_data(
    session: AsyncSession,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    base_dir=None,
) -> dict:
    """Delete snapshots/diffs older than the retention window and orphaned refs.

    Docs are retained indefinitely — they are the authoritative materialized view.
    Blob cleanup is backend-agnostic: the configured store (local disk or
    Cloudinary) is responsible for deleting the underlying assets.
    """
    cutoff = _now() - timedelta(days=retention_days)
    store = SnapshotStore(base_dir) if base_dir is not None else get_snapshot_store()

    expired = list(
        await session.scalars(
            select(Snapshot).where(Snapshot.fetched_at < cutoff)
        )
    )
    for snapshot in expired:
        for ref in (snapshot.raw_storage_ref, snapshot.screenshot_storage_ref):
            if not ref:
                continue
            try:
                store.delete_raw(ref)
            except Exception:
                pass

    if expired:
        await session.execute(
            delete(Diff).where(Diff.created_at < cutoff)
        )
        for snapshot in expired:
            await session.delete(snapshot)
        await session.flush()

    orphaned_files = 0
    known_refs = set(
        (await session.scalars(select(Snapshot.raw_storage_ref))).all()
    ) | set(
        (
            await session.scalars(
                select(Snapshot.screenshot_storage_ref).where(
                    Snapshot.screenshot_storage_ref.isnot(None)
                )
            )
        ).all()
    )
    for ref in store.list_refs():
        if ref in known_refs:
            continue
        try:
            store.delete_raw(ref)
            orphaned_files += 1
        except Exception:
            pass

    await session.commit()
    return {
        "snapshots_deleted": len(expired),
        "orphaned_files_deleted": orphaned_files,
    }
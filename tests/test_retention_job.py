from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.jobs.retention import cleanup_expired_data
from app.models.diff import Diff
from app.models.doc import Doc
from app.models.organization import Organization
from app.models.snapshot import Snapshot
from app.models.source import Source
from app.storage.snapshot_store import SnapshotStore


@pytest.fixture
def snapshot_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCVERSION_SNAPSHOT_STORAGE_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


async def _make_source(session, name: str) -> Source:
    org = Organization(name=name, slug=name.lower())
    session.add(org)
    await session.flush()
    source = Source(
        org_id=org.id,
        name=name,
        type="scrape",
        target_url="https://example.com/page",
        is_active=True,
    )
    session.add(source)
    await session.flush()
    return source


@pytest.mark.anyio
async def test_retention_deletes_snapshots_older_than_90_days(
    session_factory, snapshot_dir
) -> None:
    old = datetime.now(timezone.utc) - timedelta(days=120)

    async with session_factory() as session:
        source = await _make_source(session, "Retention Org")
        store = SnapshotStore(snapshot_dir)

        fresh = Snapshot(
            source_id=source.id, content_hash="fresh", raw_storage_ref="", fetched_at=None
        )
        session.add(fresh)
        await session.flush()
        fresh.raw_storage_ref = store.write_raw(fresh.id, b"fresh")

        stale = Snapshot(
            source_id=source.id, content_hash="stale", raw_storage_ref="", fetched_at=old
        )
        session.add(stale)
        await session.flush()
        stale.raw_storage_ref = store.write_raw(stale.id, b"stale")
        await session.commit()

    async with session_factory() as session:
        result = await cleanup_expired_data(session, retention_days=90, base_dir=snapshot_dir)
        assert result["snapshots_deleted"] == 1

        remaining = list((await session.scalars(select(Snapshot))).all())
        assert len(remaining) == 1
        assert remaining[0].content_hash == "fresh"
        assert (snapshot_dir / f"{remaining[0].id}.raw").exists()
        assert not (snapshot_dir / f"{stale.id}.raw").exists()


@pytest.mark.anyio
async def test_retention_deletes_orphaned_raw_files(session_factory, snapshot_dir) -> None:
    async with session_factory() as session:
        source = await _make_source(session, "Orphan Org")
        store = SnapshotStore(snapshot_dir)
        snapshot = Snapshot(
            source_id=source.id, content_hash="h", raw_storage_ref="", fetched_at=None
        )
        session.add(snapshot)
        await session.flush()
        snapshot.raw_storage_ref = store.write_raw(snapshot.id, b"kept")
        await session.commit()

        store.write_raw(9999, b"orphan-raw")
        store.write_raw(9998, b"orphan-shot", suffix="png")
        assert (snapshot_dir / "9999.raw").exists()

    async with session_factory() as session:
        await cleanup_expired_data(session, retention_days=90, base_dir=snapshot_dir)

    assert not (snapshot_dir / "9999.raw").exists()
    assert not (snapshot_dir / "9998.png").exists()
    assert (snapshot_dir / f"{snapshot.id}.raw").exists()


@pytest.mark.anyio
async def test_retention_preserves_docs_indefinitely(session_factory, snapshot_dir) -> None:
    async with session_factory() as session:
        source = await _make_source(session, "Preserve Org")
        doc = Doc(
            org_id=source.org_id,
            source_id=source.id,
            title="Kept Doc",
            slug="kept-doc",
            current_content_md="body",
            version=5,
        )
        session.add(doc)
        await session.commit()
        doc_id = doc.id

    async with session_factory() as session:
        await cleanup_expired_data(session, retention_days=90, base_dir=snapshot_dir)

    async with session_factory() as session:
        kept = await session.get(Doc, doc_id)
        assert kept is not None
        assert kept.version == 5

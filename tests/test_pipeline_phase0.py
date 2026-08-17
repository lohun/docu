import pytest
from sqlalchemy import func, select

from app.adapters.base import AdapterResult
from app.config import get_settings
from app.models.organization import Organization
from app.models.snapshot import Snapshot
from app.models.source import Source
from app.scheduler import pipeline as pipeline_module
from app.scheduler.pipeline import trigger_pipeline_run


class _FixedAdapter:
    def __init__(self, content: str) -> None:
        self.content = content

    async def fetch(self, source: Source) -> AdapterResult:
        return AdapterResult(
            normalized=self.content,
            raw_bytes=self.content.encode("utf-8"),
            excerpt=self.content[:2048],
        )


@pytest.fixture
def snapshot_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCVERSION_SNAPSHOT_STORAGE_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_first_run_creates_baseline_snapshot_no_diff(
    session_factory, snapshot_dir, monkeypatch
) -> None:
    adapter = _FixedAdapter('{"openapi": "3.0.0"}')

    def mock_get_adapter(source_type: str):
        return adapter

    monkeypatch.setattr(pipeline_module, "get_adapter", mock_get_adapter)

    async with session_factory() as session:
        org = Organization(name="Baseline Org", slug="baseline-org")
        session.add(org)
        await session.flush()

        source = Source(
            org_id=org.id,
            name="Baseline Source",
            type="openapi",
            target_url="https://example.com/openapi.json",
            is_active=True,
        )
        session.add(source)
        await session.commit()

        run_log = await trigger_pipeline_run(session, source.id)
        assert run_log.outcome == "success"

        snapshots = list(
            await session.scalars(select(Snapshot).where(Snapshot.source_id == source.id))
        )
        assert len(snapshots) == 1
        assert snapshots[0].content_hash is not None
        assert snapshots[0].raw_storage_ref.endswith(".raw")


@pytest.mark.anyio
async def test_unchanged_hash_skips_diff_and_llm(
    session_factory, snapshot_dir, monkeypatch
) -> None:
    adapter = _FixedAdapter("stable content")

    monkeypatch.setattr(pipeline_module, "get_adapter", lambda _t: adapter)

    async with session_factory() as session:
        org = Organization(name="NoChange Org", slug="nochange-org")
        session.add(org)
        await session.flush()

        source = Source(
            org_id=org.id,
            name="NoChange Source",
            type="scrape",
            target_url="https://example.com/page",
            is_active=True,
        )
        session.add(source)
        await session.commit()

        first = await trigger_pipeline_run(session, source.id)
        second = await trigger_pipeline_run(session, source.id)

        assert first.outcome == "success"
        assert second.outcome == "success_no_change"

        count = await session.scalar(
            select(func.count()).select_from(Snapshot).where(Snapshot.source_id == source.id)
        )
        assert count == 1


@pytest.mark.anyio
async def test_snapshot_raw_file_written_to_configured_dir(
    session_factory, snapshot_dir, monkeypatch
) -> None:
    content = "raw snapshot bytes"
    monkeypatch.setattr(
        pipeline_module,
        "get_adapter",
        lambda _t: _FixedAdapter(content),
    )

    async with session_factory() as session:
        org = Organization(name="Storage Org", slug="storage-org")
        session.add(org)
        await session.flush()

        source = Source(
            org_id=org.id,
            name="Storage Source",
            type="openapi",
            target_url="https://example.com/spec.json",
            is_active=True,
        )
        session.add(source)
        await session.commit()

        await trigger_pipeline_run(session, source.id)

        snapshot = await session.scalar(
            select(Snapshot).where(Snapshot.source_id == source.id)
        )
        assert snapshot is not None
        raw_path = snapshot_dir / f"{snapshot.id}.raw"
        assert raw_path.exists()
        assert raw_path.read_bytes() == content.encode("utf-8")


@pytest.mark.anyio
async def test_pipeline_selects_adapter_by_source_type(
    session_factory, snapshot_dir, monkeypatch
) -> None:
    selected: list[str] = []

    def tracking_get_adapter(source_type: str):
        selected.append(source_type)
        return _FixedAdapter(f"content-for-{source_type}")

    monkeypatch.setattr(pipeline_module, "get_adapter", tracking_get_adapter)

    async with session_factory() as session:
        org = Organization(name="Adapter Org", slug="adapter-org")
        session.add(org)
        await session.flush()

        for source_type in ("openapi", "scrape", "webapp"):
            source = Source(
                org_id=org.id,
                name=f"{source_type} source",
                type=source_type,
                target_url="https://example.com/target",
                is_active=True,
            )
            session.add(source)
            await session.flush()
            await trigger_pipeline_run(session, source.id)

        assert selected == ["openapi", "scrape", "webapp"]


class _ScreenshotAdapter:
    def __init__(self, content: str) -> None:
        self.content = content
        self.screenshot = b"\x89PNG-screenshot"

    async def fetch(self, source: Source) -> AdapterResult:
        return AdapterResult(
            normalized=self.content,
            raw_bytes=self.content.encode("utf-8"),
            excerpt=self.content[:2048],
            screenshot=self.screenshot,
        )


@pytest.mark.anyio
async def test_pipeline_persists_screenshot_ref(
    session_factory, snapshot_dir, monkeypatch
) -> None:
    monkeypatch.setattr(pipeline_module, "get_adapter", lambda _t: _ScreenshotAdapter("page"))

    async with session_factory() as session:
        org = Organization(name="Screenshot Org", slug="screenshot-org")
        session.add(org)
        await session.flush()

        source = Source(
            org_id=org.id,
            name="Screenshot Source",
            type="scrape",
            target_url="https://example.com/page",
            is_active=True,
        )
        session.add(source)
        await session.commit()

        await trigger_pipeline_run(session, source.id)

        snapshot = await session.scalar(
            select(Snapshot).where(Snapshot.source_id == source.id)
        )
        assert snapshot is not None
        assert snapshot.raw_storage_ref.endswith(".raw")
        assert snapshot.screenshot_storage_ref is not None
        assert snapshot.screenshot_storage_ref.endswith(".png")
        assert (snapshot_dir / f"{snapshot.id}.png").exists()

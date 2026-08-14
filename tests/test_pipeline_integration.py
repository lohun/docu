import pytest
from sqlalchemy import select

from app.adapters.base import AdapterResult
from app.config import get_settings
from app.llm.client import InitialDoc, SectionUpdate
from app.models.diff import Diff
from app.models.doc import Doc
from app.models.doc_update import DocUpdate
from app.models.organization import Organization
from app.models.snapshot import Snapshot
from app.models.source import Source
from app.scheduler import pipeline as pipeline_module
from app.scheduler.pipeline import trigger_pipeline_run

SPEC_V1 = (
    '{"info":{"title":"Pet Store","version":"1.0.0"},'
    '"openapi":"3.0.0","paths":{"/pets":{"get":{"operationId":"listPets"}}}}'
)
SPEC_V2 = (
    '{"info":{"title":"Pet Store","version":"1.1.0"},'
    '"openapi":"3.0.0","paths":{"/pets":{"get":{"operationId":"listPets"},'
    '"post":{"operationId":"createPet"}}}}'
)

BASE_TEXT = "\n".join(f"line {i}" for i in range(100))
WHITESPACE_TEXT = "\n".join(f"  line {i}  " for i in range(100))


class _StatefulAdapter:
    def __init__(self, contents: list[str], screenshot: bytes | None = None) -> None:
        self.contents = contents
        self.index = 0
        self.screenshot = screenshot

    async def fetch(self, source: Source) -> AdapterResult:
        content = self.contents[min(self.index, len(self.contents) - 1)]
        self.index += 1
        return AdapterResult(
            normalized=content,
            raw_bytes=content.encode("utf-8"),
            excerpt=content[:2048],
            screenshot=self.screenshot,
        )


class _FakeLLMClient:
    model = "test-model"

    def is_available(self) -> bool:
        return True


class _FakeQueue:
    def __init__(self) -> None:
        self.calls = 0
        self.initial_doc_calls = 0

    async def generate_section_update(self, client, context_md, diff_payload, hint):
        self.calls += 1
        return SectionUpdate(section_key="Endpoints", new_content="New endpoints body."), 25

    async def generate_initial_doc(self, client, source_content, source_type, source_name):
        self.initial_doc_calls += 1
        return InitialDoc(full_content="# Initial Documentation\n\nThis is the initial doc."), 50


@pytest.fixture
def snapshot_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCVERSION_SNAPSHOT_STORAGE_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_openapi_source_change_triggers_diff_and_publish(
    session_factory, snapshot_dir, monkeypatch
) -> None:
    fake_queue = _FakeQueue()
    monkeypatch.setattr(pipeline_module, "LLMClient", lambda: _FakeLLMClient())
    monkeypatch.setattr(pipeline_module, "get_llm_queue", lambda: fake_queue)

    adapter = _StatefulAdapter([SPEC_V1, SPEC_V2])
    monkeypatch.setattr(pipeline_module, "get_adapter", lambda _t: adapter)

    async with session_factory() as session:
        org = Organization(name="E2E Org", slug="e2e-org")
        session.add(org)
        await session.flush()

        source = Source(
            org_id=org.id,
            name="Pet Store",
            type="openapi",
            target_url="https://example.com/spec.json",
            is_active=True,
        )
        session.add(source)
        await session.commit()

        first = await trigger_pipeline_run(session, source.id)
        second = await trigger_pipeline_run(session, source.id)

        assert first.outcome == "success_initial_doc"
        assert second.outcome == "success"

        snapshots = list(
            await session.scalars(
                select(Snapshot)
                .where(Snapshot.source_id == source.id)
                .order_by(Snapshot.id)
            )
        )
        assert len(snapshots) == 2

        diffs = list(
            await session.scalars(select(Diff).where(Diff.source_id == source.id))
        )
        assert len(diffs) == 1
        assert diffs[0].diff_type == "oasdiff"
        assert diffs[0].is_trivial is False
        assert diffs[0].diff_payload["format"] == "oasdiff"
        assert diffs[0].from_snapshot_id == snapshots[0].id
        assert diffs[0].to_snapshot_id == snapshots[1].id

        updates = list(
            await session.scalars(select(DocUpdate).where(DocUpdate.source_id == source.id))
        )
        assert len(updates) == 2
        assert updates[0].status == "initial"
        assert updates[1].diff_id == diffs[0].id
        assert updates[1].status == "published"

        doc = await session.scalar(
            select(Doc).where(Doc.org_id == org.id, Doc.source_id == source.id)
        )
        assert doc is not None
        assert doc.version == 2
        assert "New endpoints body." in doc.current_content_md
        assert fake_queue.calls == 1
        assert fake_queue.initial_doc_calls == 1


@pytest.mark.anyio
async def test_first_run_generates_initial_doc(
    session_factory, snapshot_dir, monkeypatch
) -> None:
    fake_queue = _FakeQueue()
    monkeypatch.setattr(pipeline_module, "LLMClient", lambda: _FakeLLMClient())
    monkeypatch.setattr(pipeline_module, "get_llm_queue", lambda: fake_queue)

    adapter = _StatefulAdapter([SPEC_V1])
    monkeypatch.setattr(pipeline_module, "get_adapter", lambda _t: adapter)

    async with session_factory() as session:
        org = Organization(name="Baseline Org", slug="baseline-org")
        session.add(org)
        await session.flush()

        source = Source(
            org_id=org.id,
            name="Baseline",
            type="openapi",
            target_url="https://example.com/spec.json",
            is_active=True,
        )
        session.add(source)
        await session.commit()

        run_log = await trigger_pipeline_run(session, source.id)
        assert run_log.outcome == "success_initial_doc"

        diffs = list(
            await session.scalars(select(Diff).where(Diff.source_id == source.id))
        )
        assert diffs == []

        updates = list(
            await session.scalars(select(DocUpdate).where(DocUpdate.source_id == source.id))
        )
        assert len(updates) == 1
        assert updates[0].status == "initial"

        doc = await session.scalar(
            select(Doc).where(Doc.org_id == org.id, Doc.source_id == source.id)
        )
        assert doc is not None
        assert doc.version == 1
        assert "Initial Documentation" in doc.current_content_md
        assert fake_queue.initial_doc_calls == 1


@pytest.mark.anyio
async def test_git_export_failure_does_not_rollback_db_publish(
    session_factory, snapshot_dir, monkeypatch
) -> None:
    fake_queue = _FakeQueue()
    monkeypatch.setattr(pipeline_module, "LLMClient", lambda: _FakeLLMClient())
    monkeypatch.setattr(pipeline_module, "get_llm_queue", lambda: fake_queue)

    from app.publish.git_export import GitExportError

    def boom(*args, **kwargs):
        raise GitExportError("remote unreachable")

    monkeypatch.setattr(pipeline_module, "export_doc_to_git", boom)

    adapter = _StatefulAdapter([SPEC_V1, SPEC_V2])
    monkeypatch.setattr(pipeline_module, "get_adapter", lambda _t: adapter)

    async with session_factory() as session:
        org = Organization(name="GitOrg", slug="git-org")
        session.add(org)
        await session.flush()

        source = Source(
            org_id=org.id,
            name="Pet Store",
            type="openapi",
            target_url="https://example.com/spec.json",
            is_active=True,
        )
        session.add(source)
        await session.flush()

        doc = Doc(
            org_id=org.id,
            source_id=source.id,
            title="Pet Store",
            slug="pet-store",
            current_content_md="",
            version=1,
            git_export_enabled=True,
        )
        session.add(doc)
        await session.commit()

        await trigger_pipeline_run(session, source.id)
        await trigger_pipeline_run(session, source.id)

        updates = list(
            await session.scalars(select(DocUpdate).where(DocUpdate.source_id == source.id))
        )
        assert len(updates) == 1
        assert updates[0].status == "published"

        persisted_doc = await session.get(Doc, doc.id)
        assert persisted_doc is not None
        assert persisted_doc.version == 2
        assert "New endpoints body." in persisted_doc.current_content_md


@pytest.mark.anyio
async def test_trivial_diff_skips_llm_in_pipeline(
    session_factory, snapshot_dir, monkeypatch
) -> None:
    fake_queue = _FakeQueue()
    monkeypatch.setattr(pipeline_module, "LLMClient", lambda: _FakeLLMClient())
    monkeypatch.setattr(pipeline_module, "get_llm_queue", lambda: fake_queue)

    adapter = _StatefulAdapter([BASE_TEXT, WHITESPACE_TEXT])
    monkeypatch.setattr(pipeline_module, "get_adapter", lambda _t: adapter)

    async with session_factory() as session:
        org = Organization(name="Trivial Org", slug="trivial-org")
        session.add(org)
        await session.flush()

        source = Source(
            org_id=org.id,
            name="Trivial",
            type="scrape",
            target_url="https://example.com/page",
            is_active=True,
        )
        session.add(source)
        await session.commit()

        first = await trigger_pipeline_run(session, source.id)
        second = await trigger_pipeline_run(session, source.id)

        assert first.outcome == "success_initial_doc"
        assert second.outcome == "success_trivial"
        assert fake_queue.calls == 0
        assert fake_queue.initial_doc_calls == 1

        diffs = list(
            await session.scalars(select(Diff).where(Diff.source_id == source.id))
        )
        assert len(diffs) == 1
        assert diffs[0].is_trivial is True
        assert diffs[0].diff_type == "text"

        updates = list(
            await session.scalars(select(DocUpdate).where(DocUpdate.source_id == source.id))
        )
        assert len(updates) == 1
        assert updates[0].status == "initial"


@pytest.mark.anyio
async def test_scrape_writes_screenshot_alongside_raw(
    session_factory, snapshot_dir, monkeypatch
) -> None:
    fake_queue = _FakeQueue()
    monkeypatch.setattr(pipeline_module, "LLMClient", lambda: _FakeLLMClient())
    monkeypatch.setattr(pipeline_module, "get_llm_queue", lambda: fake_queue)

    adapter = _StatefulAdapter([BASE_TEXT], screenshot=b"fake-png-bytes")
    monkeypatch.setattr(pipeline_module, "get_adapter", lambda _t: adapter)

    async with session_factory() as session:
        org = Organization(name="Shot Org", slug="shot-org")
        session.add(org)
        await session.flush()

        source = Source(
            org_id=org.id,
            name="Shot",
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
        png_path = snapshot_dir / f"{snapshot.id}.png"
        raw_path = snapshot_dir / f"{snapshot.id}.raw"
        assert raw_path.exists()
        assert png_path.exists()
        assert png_path.read_bytes() == b"fake-png-bytes"


@pytest.mark.anyio
async def test_source_run_without_doc_creates_initial_doc(
    session_factory, snapshot_dir, monkeypatch
) -> None:
    """Test that running a source without an associated doc triggers initial doc generation."""
    fake_queue = _FakeQueue()
    monkeypatch.setattr(pipeline_module, "LLMClient", lambda: _FakeLLMClient())
    monkeypatch.setattr(pipeline_module, "get_llm_queue", lambda: fake_queue)

    adapter = _StatefulAdapter([SPEC_V1])
    monkeypatch.setattr(pipeline_module, "get_adapter", lambda _t: adapter)

    async with session_factory() as session:
        org = Organization(name="Missing Doc Org", slug="missing-doc-org")
        session.add(org)
        await session.flush()

        # Create source without doc (simulating a source created before doc auto-creation)
        source = Source(
            org_id=org.id,
            name="Missing Doc",
            type="openapi",
            target_url="https://example.com/spec.json",
            is_active=True,
        )
        session.add(source)
        await session.commit()

        # Verify no doc exists
        doc = await session.scalar(
            select(Doc).where(Doc.source_id == source.id)
        )
        assert doc is None

        # Run pipeline with force_initial_doc=True
        run_log = await trigger_pipeline_run(session, source.id, force_initial_doc=True)
        assert run_log.outcome == "success_initial_doc"

        # Verify doc was created
        doc = await session.scalar(
            select(Doc).where(Doc.source_id == source.id)
        )
        assert doc is not None
        assert doc.version == 1
        assert "Initial Documentation" in doc.current_content_md

        # Verify initial doc update record
        updates = list(
            await session.scalars(select(DocUpdate).where(DocUpdate.source_id == source.id))
        )
        assert len(updates) == 1
        assert updates[0].status == "initial"
        assert fake_queue.initial_doc_calls == 1

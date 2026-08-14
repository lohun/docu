import pytest
from sqlalchemy import select

from app.config import get_settings
from app.llm.metering import get_period_usage, increment_usage
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


class _StatefulAdapter:
    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.index = 0

    async def fetch(self, source: Source):
        from app.adapters.base import AdapterResult

        content = self.contents[min(self.index, len(self.contents) - 1)]
        self.index += 1
        return AdapterResult(
            normalized=content,
            raw_bytes=content.encode("utf-8"),
            excerpt=content[:2048],
        )


class _FakeLLMClient:
    model = "test-model"

    def is_available(self) -> bool:
        return True


class _CountingQueue:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_section_update(self, client, context_md, diff_payload, hint):
        self.calls += 1
        from app.llm.client import SectionUpdate

        return SectionUpdate(section_key="Endpoints", new_content="body"), 10


@pytest.fixture
def snapshot_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCVERSION_SNAPSHOT_STORAGE_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_quota_enforcement_blocks_llm_when_over_limit(
    session_factory, snapshot_dir, monkeypatch
) -> None:
    queue = _CountingQueue()
    adapter = _StatefulAdapter([SPEC_V1, SPEC_V2])
    monkeypatch.setattr(pipeline_module, "LLMClient", lambda: _FakeLLMClient())
    monkeypatch.setattr(pipeline_module, "get_llm_queue", lambda: queue)
    monkeypatch.setattr(pipeline_module, "get_adapter", lambda _t: adapter)

    async with session_factory() as session:
        org = Organization(name="Quota Org", slug="quota-enforce-org")
        session.add(org)
        await session.flush()

        usage = await get_period_usage(session, org.id)
        usage.token_quota = 5
        usage.tokens_used = 5
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

        await trigger_pipeline_run(session, source.id)
        second = await trigger_pipeline_run(session, source.id)

        assert second.outcome == "success_quota_rejected"
        assert queue.calls == 0

        updates = list(
            await session.scalars(select(DocUpdate).where(DocUpdate.source_id == source.id))
        )
        assert len(updates) == 1
        assert updates[0].status == "rejected"
        assert updates[0].diff_id is not None

        published = list(
            await session.scalars(
                select(DocUpdate).where(
                    DocUpdate.source_id == source.id,
                    DocUpdate.status == "published",
                )
            )
        )
        assert published == []


@pytest.mark.anyio
async def test_quota_under_limit_allows_llm(session_factory, snapshot_dir, monkeypatch) -> None:
    queue = _CountingQueue()
    adapter = _StatefulAdapter([SPEC_V1, SPEC_V2])
    monkeypatch.setattr(pipeline_module, "LLMClient", lambda: _FakeLLMClient())
    monkeypatch.setattr(pipeline_module, "get_llm_queue", lambda: queue)
    monkeypatch.setattr(pipeline_module, "get_adapter", lambda _t: adapter)

    async with session_factory() as session:
        org = Organization(name="Open Org", slug="quota-open-org")
        session.add(org)
        await session.flush()

        usage = await get_period_usage(session, org.id)
        usage.token_quota = 100000
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

        await trigger_pipeline_run(session, source.id)
        second = await trigger_pipeline_run(session, source.id)

        assert second.outcome == "success"
        assert queue.calls == 1
        assert await increment_usage(session, org.id, 0) is not None

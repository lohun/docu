import pytest
from sqlalchemy import select

from app.adapters.base import AdapterResult
from app.auth.tokens import create_access_token
from app.config import get_settings
from app.llm.client import SectionUpdate
from app.models.doc import Doc
from app.models.doc_update import DocUpdate
from app.models.org_membership import OrgMembership
from app.models.organization import Organization
from app.models.source import Source
from app.models.user import User
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
    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.index = 0

    async def fetch(self, source: Source) -> AdapterResult:
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


class _FakeQueue:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_section_update(self, client, context_md, diff_payload, hint):
        self.calls += 1
        return SectionUpdate(section_key="Endpoints", new_content="New endpoints body."), 25


@pytest.fixture
def snapshot_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCVERSION_SNAPSHOT_STORAGE_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def fake_llm(monkeypatch):
    fake_queue = _FakeQueue()
    monkeypatch.setattr(pipeline_module, "LLMClient", lambda: _FakeLLMClient())
    monkeypatch.setattr(pipeline_module, "get_llm_queue", lambda: fake_queue)
    return fake_queue


async def _create_tenant(
    session,
    *,
    email: str,
    org_name: str,
    org_slug: str,
    source_type: str,
    target_url: str,
    source_name: str,
    doc_slug: str,
) -> tuple[User, Organization, Source, Doc]:
    user = User(email=email, password_hash="hash", is_active=True)
    session.add(user)
    await session.flush()

    org = Organization(name=org_name, slug=org_slug)
    session.add(org)
    await session.flush()
    session.add(OrgMembership(user_id=user.id, org_id=org.id, role="owner"))

    source = Source(
        org_id=org.id,
        name=source_name,
        type=source_type,
        target_url=target_url,
        is_active=True,
    )
    session.add(source)
    await session.flush()

    doc = Doc(
        org_id=org.id,
        source_id=source.id,
        title=source_name,
        slug=doc_slug,
        current_content_md="",
        version=1,
    )
    session.add(doc)
    await session.flush()
    source.doc_target_id = doc.id
    await session.commit()
    return user, org, source, doc


@pytest.mark.anyio
async def test_e2e_openapi_source_change_triggers_publish(
    client, session_factory, snapshot_dir, fake_llm, monkeypatch
) -> None:
    adapter = _StatefulAdapter([SPEC_V1, SPEC_V2])
    monkeypatch.setattr(pipeline_module, "get_adapter", lambda _t: adapter)

    async with session_factory() as session:
        user, org, source, doc = await _create_tenant(
            session,
            email="e2e@example.com",
            org_name="E2E Org",
            org_slug="e2e-org",
            source_type="openapi",
            target_url="https://example.com/spec.json",
            source_name="Pet Store",
            doc_slug="pet-store",
        )

        first = await trigger_pipeline_run(session, source.id)
        second = await trigger_pipeline_run(session, source.id)
        assert first.outcome == "success"
        assert second.outcome == "success"

        persisted = await session.get(Doc, doc.id)
        assert persisted is not None
        assert persisted.version == 2
        assert "New endpoints body." in persisted.current_content_md

    token = create_access_token(user.id)
    client.cookies.set("access_token", token)

    resp = await client.get(f"/orgs/{org.id}/docs/{doc.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 2
    assert "New endpoints body." in body["current_content_md"]

    resp = await client.get(f"/orgs/{org.id}/docs/{doc.id}/history")
    assert resp.status_code == 200
    updates = resp.json()
    assert len(updates) == 1
    assert updates[0]["status"] == "published"
    assert updates[0]["section_key"] == "Endpoints"

    resp = await client.get(
        f"/orgs/{org.id}/docs/{doc.id}/diffs/{updates[0]['diff_id']}",
    )
    assert resp.status_code == 200
    diff_view = resp.json()
    assert diff_view["diff_type"] == "oasdiff"
    assert diff_view["resulting_update"]["new_content"] == "New endpoints body."


@pytest.mark.anyio
async def test_e2e_scrape_source_cosmetic_change_skips_llm(
    client, session_factory, snapshot_dir, fake_llm, monkeypatch
) -> None:
    adapter = _StatefulAdapter([BASE_TEXT, WHITESPACE_TEXT])
    monkeypatch.setattr(pipeline_module, "get_adapter", lambda _t: adapter)

    async with session_factory() as session:
        user, org, source, doc = await _create_tenant(
            session,
            email="cosmetic@example.com",
            org_name="Cosmetic Org",
            org_slug="cosmetic-org",
            source_type="scrape",
            target_url="https://example.com/page",
            source_name="Landing",
            doc_slug="landing",
        )

        first = await trigger_pipeline_run(session, source.id)
        second = await trigger_pipeline_run(session, source.id)
        assert first.outcome == "success"
        assert second.outcome == "success_trivial"
        assert fake_llm.calls == 0

        updates = list(
            await session.scalars(select(DocUpdate).where(DocUpdate.source_id == source.id))
        )
        assert updates == []

        persisted = await session.get(Doc, doc.id)
        assert persisted is not None
        assert persisted.version == 1
        assert persisted.current_content_md == ""

    token = create_access_token(user.id)
    client.cookies.set("access_token", token)

    resp = await client.get(f"/orgs/{org.id}/docs/{doc.id}")
    assert resp.status_code == 200
    assert resp.json()["version"] == 1

    resp = await client.get(f"/orgs/{org.id}/docs/{doc.id}/history")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_e2e_cross_org_isolation_in_pipeline(
    client, session_factory, snapshot_dir, fake_llm, monkeypatch
) -> None:
    holder: dict = {}
    monkeypatch.setattr(pipeline_module, "get_adapter", lambda _t: holder["adapter"])

    async with session_factory() as session:
        user_a, org_a, source_a, doc_a = await _create_tenant(
            session,
            email="alpha@example.com",
            org_name="Alpha",
            org_slug="alpha",
            source_type="openapi",
            target_url="https://example.com/a.json",
            source_name="A API",
            doc_slug="a-api",
        )
        user_b, org_b, source_b, doc_b = await _create_tenant(
            session,
            email="beta@example.com",
            org_name="Beta",
            org_slug="beta",
            source_type="openapi",
            target_url="https://example.com/b.json",
            source_name="B API",
            doc_slug="b-api",
        )

        holder["adapter"] = _StatefulAdapter([SPEC_V1, SPEC_V2])
        run_a1 = await trigger_pipeline_run(session, source_a.id)
        run_a2 = await trigger_pipeline_run(session, source_a.id)
        assert run_a1.outcome == "success"
        assert run_a2.outcome == "success"

        holder["adapter"] = _StatefulAdapter([SPEC_V1, SPEC_V2])
        run_b1 = await trigger_pipeline_run(session, source_b.id)
        run_b2 = await trigger_pipeline_run(session, source_b.id)
        assert run_b1.outcome == "success"
        assert run_b2.outcome == "success"

        doc_a = await session.get(Doc, doc_a.id)
        doc_b = await session.get(Doc, doc_b.id)
        assert doc_a is not None and doc_b is not None
        assert "New endpoints body." in doc_a.current_content_md
        assert "New endpoints body." in doc_b.current_content_md

        updates_b = list(
            await session.scalars(
                select(DocUpdate).where(DocUpdate.source_id == source_b.id)
            )
        )
        assert len(updates_b) == 1
        assert updates_b[0].doc_id == doc_b.id

    token_a = create_access_token(user_a.id)
    token_b = create_access_token(user_b.id)

    # Org A owns its doc.
    client.cookies.set("access_token", token_a)
    resp = await client.get(
        f"/orgs/{org_a.id}/docs/{doc_a.id}",
    )
    assert resp.status_code == 200

    # Org B cannot read Org A's doc through the API.
    client.cookies.set("access_token", token_b)
    resp = await client.get(
        f"/orgs/{org_a.id}/docs/{doc_a.id}",
    )
    assert resp.status_code == 403

    # Org B can read its own doc.
    resp = await client.get(
        f"/orgs/{org_b.id}/docs/{doc_b.id}",
    )
    assert resp.status_code == 200

    # Org A cannot list Org B's docs.
    client.cookies.set("access_token", token_a)
    resp = await client.get(
        f"/orgs/{org_b.id}/docs",
    )
    assert resp.status_code == 403

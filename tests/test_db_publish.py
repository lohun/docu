import pytest
from sqlalchemy import select

from app.llm.client import SectionUpdate
from app.models.diff import Diff
from app.models.doc import Doc
from app.models.doc_update import DocUpdate
from app.models.org_membership import OrgMembership
from app.models.organization import Organization
from app.models.snapshot import Snapshot
from app.models.source import Source
from app.publish.db_publish import publish_to_db, resolve_doc


@pytest.fixture
async def _fixtures(session_factory):
    async with session_factory() as session:
        org = Organization(name="Publish Org", slug="publish-org")
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
            current_content_md="# Pet Store\n\n## Introduction\n\nIntro text.\n",
            version=1,
        )
        session.add(doc)
        await session.flush()


        snapshot = Snapshot(
            source_id=source.id,
            content_hash="abc123",
            normalized_excerpt="excerpt",
            raw_storage_ref="/tmp/x.raw",
        )
        session.add(snapshot)
        await session.flush()

        diff = Diff(
            source_id=source.id,
            to_snapshot_id=snapshot.id,
            diff_type="oasdiff",
            diff_payload={"format": "oasdiff", "changes": []},
            is_trivial=False,
        )
        session.add(diff)
        await session.flush()

        await session.commit()
        yield {"org": org, "doc": doc, "source": source, "diff": diff}


@pytest.mark.anyio
async def test_publish_updates_docs_current_content_md_and_version(session_factory, _fixtures) -> None:
    async with session_factory() as session:
        org = await session.get(Organization, _fixtures["org"].id)
        doc = await session.get(Doc, _fixtures["doc"].id)
        source = await session.get(Source, _fixtures["source"].id)
        diff = await session.get(Diff, _fixtures["diff"].id)

        update = await publish_to_db(
            session,
            source,
            doc,
            diff,
            SectionUpdate(section_key="Introduction", new_content="Updated intro."),
            llm_model_used="llama-3.3-70b",
            token_usage=120,
        )

        await session.refresh(doc)
        assert doc.version == 2
        assert "Updated intro." in doc.current_content_md
        assert "Intro text." not in doc.current_content_md
        assert update.status == "published"


@pytest.mark.anyio
async def test_publish_appends_doc_updates_row(session_factory, _fixtures) -> None:
    async with session_factory() as session:
        org = await session.get(Organization, _fixtures["org"].id)
        doc = await session.get(Doc, _fixtures["doc"].id)
        source = await session.get(Source, _fixtures["source"].id)
        diff = await session.get(Diff, _fixtures["diff"].id)

        await publish_to_db(
            session,
            source,
            doc,
            diff,
            SectionUpdate(section_key="Introduction", new_content="Updated intro."),
            llm_model_used="llama-3.3-70b",
            token_usage=120,
        )

        rows = list(
            await session.scalars(select(DocUpdate).where(DocUpdate.doc_id == doc.id))
        )
        assert len(rows) == 1
        assert rows[0].new_content == "Updated intro."
        assert rows[0].previous_content == "Intro text."
        assert rows[0].llm_model_used == "llama-3.3-70b"
        assert rows[0].token_usage == 120


@pytest.mark.anyio
async def test_publish_links_diff_id_and_source_id(session_factory, _fixtures) -> None:
    async with session_factory() as session:
        org = await session.get(Organization, _fixtures["org"].id)
        doc = await session.get(Doc, _fixtures["doc"].id)
        source = await session.get(Source, _fixtures["source"].id)
        diff = await session.get(Diff, _fixtures["diff"].id)

        update = await publish_to_db(
            session,
            source,
            doc,
            diff,
            SectionUpdate(section_key="Introduction", new_content="New body."),
        )
        assert update.diff_id == diff.id
        assert update.source_id == source.id


@pytest.mark.anyio
async def test_publish_is_idempotent_on_retry_same_diff(session_factory, _fixtures) -> None:
    async with session_factory() as session:
        org = await session.get(Organization, _fixtures["org"].id)
        doc = await session.get(Doc, _fixtures["doc"].id)
        source = await session.get(Source, _fixtures["source"].id)
        diff = await session.get(Diff, _fixtures["diff"].id)

        update = SectionUpdate(section_key="Introduction", new_content="Once only.")
        first = await publish_to_db(session, source, doc, diff, update)
        second = await publish_to_db(session, source, doc, diff, update)

        await session.refresh(doc)
        assert first.id == second.id
        assert doc.version == 2
        rows = list(
            await session.scalars(select(DocUpdate).where(DocUpdate.diff_id == diff.id))
        )
        assert len(rows) == 1


@pytest.mark.anyio
async def test_publish_auto_creates_doc_when_no_target(session_factory) -> None:
    async with session_factory() as session:
        org = Organization(name="Auto Org", slug="auto-org")
        session.add(org)
        await session.flush()

        source = Source(
            org_id=org.id,
            name="Auto Source",
            type="scrape",
            target_url="https://example.com/docs",
            is_active=True,
        )
        session.add(source)
        await session.flush()

        doc = await resolve_doc(session, source)
        assert doc.id is not None
        assert doc.source_id == source.id

        await session.commit()
        persisted = await session.scalar(
            select(Doc).where(Doc.id == doc.id)
        )
        assert persisted is not None

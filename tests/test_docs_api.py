from datetime import datetime, timezone

import pytest
from httpx import AsyncClient

from app.auth.tokens import create_access_token
from app.models.diff import Diff
from app.models.doc import Doc
from app.models.doc_update import DocUpdate
from app.models.org_membership import OrgMembership
from app.models.organization import Organization
from app.models.snapshot import Snapshot
from app.models.source import Source
from app.models.user import User


@pytest.fixture
async def docs_fixtures(session_factory):
    async with session_factory() as session:
        user = User(email="docs_owner@example.com", password_hash="hash", is_active=True)
        session.add(user)
        await session.flush()

        org = Organization(name="Docs Org", slug="docs-org")
        session.add(org)
        await session.flush()

        session.add(OrgMembership(user_id=user.id, org_id=org.id, role="owner"))

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
            current_content_md="# Pet Store\n\n## Endpoints\n\nBody.",
            version=2,
            git_export_enabled=False,
        )
        session.add(doc)
        await session.flush()

        snap = Snapshot(
            source_id=source.id,
            content_hash="abc",
            normalized_excerpt="excerpt",
            raw_storage_ref="/tmp/x.raw",
        )
        session.add(snap)
        await session.flush()

        diff = Diff(
            source_id=source.id,
            to_snapshot_id=snap.id,
            diff_type="oasdiff",
            diff_payload={"format": "oasdiff", "changes": []},
            is_trivial=False,
        )
        session.add(diff)
        await session.flush()

        update = DocUpdate(
            source_id=source.id,
            diff_id=diff.id,
            doc_id=doc.id,
            section_key="Endpoints",
            previous_content="Old body.",
            new_content="Body.",
            llm_model_used="llama-3.3-70b",
            token_usage=80,
            status="published",
        )
        session.add(update)
        await session.commit()

        yield {
            "user": user,
            "org": org,
            "doc": doc,
            "diff": diff,
            "update": update,
        }


@pytest.mark.anyio
async def test_list_docs_scoped_to_org(client: AsyncClient, docs_fixtures) -> None:
    token = create_access_token(docs_fixtures["user"].id)
    client.cookies.set("access_token", token)

    resp = await client.get(
        f"/orgs/{docs_fixtures['org'].id}/docs"
    )
    assert resp.status_code == 200
    docs = resp.json()
    assert len(docs) == 1
    assert docs[0]["slug"] == "pet-store"
    assert docs[0]["version"] == 2


@pytest.mark.anyio
async def test_get_doc_returns_current_content_md(client: AsyncClient, docs_fixtures) -> None:
    token = create_access_token(docs_fixtures["user"].id)
    client.cookies.set("access_token", token)

    resp = await client.get(
        f"/orgs/{docs_fixtures['org'].id}/docs/{docs_fixtures['doc'].id}",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "Pet Store" in data["current_content_md"]
    assert data["title"] == "Pet Store"


@pytest.mark.anyio
async def test_get_doc_version_history_from_doc_updates(
    client: AsyncClient, docs_fixtures
) -> None:
    token = create_access_token(docs_fixtures["user"].id)
    client.cookies.set("access_token", token)

    resp = await client.get(
        f"/orgs/{docs_fixtures['org'].id}/docs/{docs_fixtures['doc'].id}/history",
    )
    assert resp.status_code == 200
    history = resp.json()
    assert len(history) == 1
    assert history[0]["section_key"] == "Endpoints"
    assert history[0]["new_content"] == "Body."
    assert history[0]["llm_model_used"] == "llama-3.3-70b"


@pytest.mark.anyio
async def test_get_diff_view_links_diff_to_doc_update(
    client: AsyncClient, docs_fixtures
) -> None:
    token = create_access_token(docs_fixtures["user"].id)
    client.cookies.set("access_token", token)

    resp = await client.get(
        f"/orgs/{docs_fixtures['org'].id}/docs/{docs_fixtures['doc'].id}"
        f"/diffs/{docs_fixtures['diff'].id}",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["diff_type"] == "oasdiff"
    assert data["diff_payload"]["format"] == "oasdiff"
    assert data["resulting_update"]["id"] == docs_fixtures["update"].id
    assert data["resulting_update"]["section_key"] == "Endpoints"


@pytest.mark.anyio
async def test_docs_are_org_scoped(client: AsyncClient, session_factory, docs_fixtures) -> None:
    async with session_factory() as session:
        other = Organization(name="Other Org", slug="other-org")
        session.add(other)
        await session.flush()

        other_doc = Doc(
            org_id=other.id,
            source_id=docs_fixtures["doc"].source_id,
            title="Other Doc",
            slug="other",
            current_content_md="other",
            version=1,
        )
        session.add(other_doc)
        await session.commit()
        other_doc_id = other_doc.id

    token = create_access_token(docs_fixtures["user"].id)
    client.cookies.set("access_token", token)

    resp = await client.get(
        f"/orgs/{docs_fixtures['org'].id}/docs/{other_doc_id}"
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_resolve_org_by_custom_domain_when_verified(
    client: AsyncClient, session_factory, docs_fixtures
) -> None:
    async with session_factory() as session:
        org = await session.get(Organization, docs_fixtures["org"].id)
        org.custom_domain = "docs.acme.example"
        org.custom_domain_verified_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await session.commit()

    resp = await client.get(
        "/docs/pet-store",
        headers={"host": "docs.acme.example"},
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Pet Store"


@pytest.mark.anyio
async def test_unverified_custom_domain_not_resolved(
    client: AsyncClient, session_factory, docs_fixtures
) -> None:
    async with session_factory() as session:
        org = await session.get(Organization, docs_fixtures["org"].id)
        org.custom_domain = "docs.pending.example"
        org.custom_domain_verified_at = None
        await session.commit()

    resp = await client.get(
        "/docs/pet-store",
        headers={"host": "docs.pending.example"},
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_source_runs_endpoint(client: AsyncClient, session_factory, docs_fixtures) -> None:
    from app.models.run_log import RunLog

    async with session_factory() as session:
        source = await session.get(Source, docs_fixtures["doc"].source_id)
        session.add(
            RunLog(source_id=source.id, outcome="success")
        )
        await session.commit()

    token = create_access_token(docs_fixtures["user"].id)
    client.cookies.set("access_token", token)

    resp = await client.get(
        f"/orgs/{docs_fixtures['org'].id}/sources/{source.id}/runs",
    )
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) == 1
    assert runs[0]["outcome"] == "success"


@pytest.mark.anyio
async def test_doc_history_404_for_unknown_doc(
    client: AsyncClient, docs_fixtures
) -> None:
    token = create_access_token(docs_fixtures["user"].id)
    client.cookies.set("access_token", token)

    resp = await client.get(
        f"/orgs/{docs_fixtures['org'].id}/docs/999999/history",
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_diff_view_without_resulting_update(
    client: AsyncClient, session_factory, docs_fixtures
) -> None:
    async with session_factory() as session:
        source = await session.get(Source, docs_fixtures["doc"].source_id)
        trivial = Diff(
            source_id=source.id,
            to_snapshot_id=docs_fixtures["diff"].to_snapshot_id,
            diff_type="text",
            diff_payload={"format": "text"},
            is_trivial=True,
        )
        session.add(trivial)
        await session.commit()
        trivial_id = trivial.id

    token = create_access_token(docs_fixtures["user"].id)
    client.cookies.set("access_token", token)

    resp = await client.get(
        f"/orgs/{docs_fixtures['org'].id}/docs/{docs_fixtures['doc'].id}"
        f"/diffs/{trivial_id}",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_trivial"] is True
    assert data["resulting_update"] is None


@pytest.mark.anyio
async def test_public_doc_404_for_unknown_slug(
    client: AsyncClient, session_factory, docs_fixtures
) -> None:
    async with session_factory() as session:
        org = await session.get(Organization, docs_fixtures["org"].id)
        org.custom_domain = "docs.acme.example"
        org.custom_domain_verified_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await session.commit()

    resp = await client.get("/docs/does-not-exist", headers={"host": "docs.acme.example"})
    assert resp.status_code == 404

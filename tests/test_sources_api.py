import pytest
from httpx import AsyncClient

from app.auth.tokens import create_access_token
from app.models.org_membership import OrgMembership
from app.models.organization import Organization
from app.models.source import Source
from app.models.user import User


@pytest.mark.anyio
async def test_sources_crud_flow(client: AsyncClient, session_factory) -> None:
    async with session_factory() as session:
        user = User(email="source_owner@example.com", password_hash="hash", is_active=True)
        session.add(user)
        await session.flush()

        org = Organization(name="Source Org", slug="source-org")
        session.add(org)
        await session.flush()

        mem = OrgMembership(user_id=user.id, org_id=org.id, role="owner")
        session.add(mem)
        await session.commit()

        token = create_access_token(user.id)
        client.cookies.set("access_token", token)

    # 1. Create Source
    create_payload = {
        "name": "Stripe OpenAPI Spec",
        "type": "openapi",
        "target_url": "https://example.com/openapi.json",
        "fetch_interval_seconds": 300,
    }
    resp = await client.post(f"/orgs/{org.id}/sources", json=create_payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Stripe OpenAPI Spec"
    assert data["org_id"] == org.id
    source_id = data["id"]

    # 2. List Sources
    list_resp = await client.get(f"/orgs/{org.id}/sources")
    assert list_resp.status_code == 200
    sources = list_resp.json()
    assert len(sources) == 1
    assert sources[0]["id"] == source_id

    # 3. Get Source Details
    get_resp = await client.get(f"/orgs/{org.id}/sources/{source_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == source_id

    # 4. Update Source
    patch_resp = await client.patch(
        f"/orgs/{org.id}/sources/{source_id}",
        json={"name": "Updated Spec Name", "fetch_interval_seconds": 600},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "Updated Spec Name"
    assert patch_resp.json()["fetch_interval_seconds"] == 600

    # 5. Delete Source
    del_resp = await client.delete(f"/orgs/{org.id}/sources/{source_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"


@pytest.mark.anyio
async def test_sources_ssrf_and_validation_errors(client: AsyncClient, session_factory) -> None:
    async with session_factory() as session:
        user = User(email="val_user@example.com", password_hash="hash", is_active=True)
        session.add(user)
        await session.flush()

        org = Organization(name="Val Org", slug="val-org")
        session.add(org)
        await session.flush()

        session.add(OrgMembership(user_id=user.id, org_id=org.id, role="admin"))
        await session.commit()

        token = create_access_token(user.id)
        client.cookies.set("access_token", token)

    # SSRF blocked target URL -> 400 Bad Request
    bad_ssrf = {
        "name": "Internal Scrape",
        "type": "scrape",
        "target_url": "http://127.0.0.1/admin",
        "fetch_interval_seconds": 300,
    }
    resp = await client.post(f"/orgs/{org.id}/sources", json=bad_ssrf)
    assert resp.status_code == 400

    # Interval below 300s -> 422 Validation Error
    bad_interval = {
        "name": "Fast Scrape",
        "type": "scrape",
        "target_url": "https://example.com/docs",
        "fetch_interval_seconds": 60,
    }
    resp2 = await client.post(f"/orgs/{org.id}/sources", json=bad_interval)
    assert resp2.status_code == 422


@pytest.mark.anyio
async def test_cross_tenant_source_isolation(client: AsyncClient, session_factory) -> None:
    async with session_factory() as session:
        u1 = User(email="u1@example.com", password_hash="hash", is_active=True)
        session.add(u1)
        await session.flush()

        org1 = Organization(name="Org One", slug="org-one")
        org2 = Organization(name="Org Two", slug="org-two")
        session.add(org1)
        session.add(org2)
        await session.flush()

        session.add(OrgMembership(user_id=u1.id, org_id=org1.id, role="owner"))
        
        # Source in org2
        s2 = Source(org_id=org2.id, name="Org2 Source", type="scrape", target_url="https://example.com/2")
        session.add(s2)
        await session.commit()

        token1 = create_access_token(u1.id)
        client.cookies.set("access_token", token1)

    # User 1 tries to access org2 sources -> 403 Forbidden
    resp = await client.get(f"/orgs/{org2.id}/sources")
    assert resp.status_code == 403

    # User 1 tries to access s2 in org1 -> 404 Not Found
    resp2 = await client.get(f"/orgs/{org1.id}/sources/{s2.id}")
    assert resp2.status_code == 404

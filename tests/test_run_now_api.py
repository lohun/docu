import pytest
from httpx import AsyncClient

from app.auth.tokens import create_access_token
from app.models.org_membership import OrgMembership
from app.models.organization import Organization
from app.models.source import Source
from app.models.user import User


@pytest.mark.anyio
async def test_run_now_endpoint(client: AsyncClient, session_factory) -> None:
    async with session_factory() as session:
        user = User(email="runner@example.com", password_hash="hash", is_active=True)
        session.add(user)
        await session.flush()

        org = Organization(name="Run Org", slug="run-org")
        session.add(org)
        await session.flush()

        session.add(OrgMembership(user_id=user.id, org_id=org.id, role="member"))

        source = Source(
            org_id=org.id,
            name="Run Source",
            type="openapi",
            target_url="https://example.com/openapi.json",
            is_active=True,
        )
        session.add(source)
        await session.commit()

        token = create_access_token(user.id)
        client.cookies.set("access_token", token)

    # Successful run-now
    resp = await client.post(f"/orgs/{org.id}/sources/{source.id}/run-now")
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "enqueued"
    assert data["source_id"] == str(source.id)


@pytest.mark.anyio
async def test_run_now_rejects_inactive_source(client: AsyncClient, session_factory) -> None:
    async with session_factory() as session:
        user = User(email="inactive_runner@example.com", password_hash="hash", is_active=True)
        session.add(user)
        await session.flush()

        org = Organization(name="Inactive Org", slug="inactive-org")
        session.add(org)
        await session.flush()

        session.add(OrgMembership(user_id=user.id, org_id=org.id, role="member"))

        source = Source(
            org_id=org.id,
            name="Inactive Source",
            type="scrape",
            target_url="https://example.com/docs",
            is_active=False,
        )
        session.add(source)
        await session.commit()

        token = create_access_token(user.id)
        client.cookies.set("access_token", token)

    resp = await client.post(f"/orgs/{org.id}/sources/{source.id}/run-now")
    assert resp.status_code == 400
    assert "inactive" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_run_now_cross_tenant_isolation(client: AsyncClient, session_factory) -> None:
    async with session_factory() as session:
        u1 = User(email="t1@example.com", password_hash="hash", is_active=True)
        session.add(u1)
        await session.flush()

        org1 = Organization(name="T1 Org", slug="t1-org")
        org2 = Organization(name="T2 Org", slug="t2-org")
        session.add(org1)
        session.add(org2)
        await session.flush()

        session.add(OrgMembership(user_id=u1.id, org_id=org1.id, role="member"))

        s2 = Source(org_id=org2.id, name="Org2 Source", type="openapi", target_url="https://example.com/2")
        session.add(s2)
        await session.commit()

        token1 = create_access_token(u1.id)
        client.cookies.set("access_token", token1)

    # User 1 attempting to trigger source belonging to Org 2 -> 403 Forbidden
    resp = await client.post(f"/orgs/{org2.id}/sources/{s2.id}/run-now")
    assert resp.status_code == 403

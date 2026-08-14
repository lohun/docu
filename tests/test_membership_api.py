import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import create_access_token
from app.models.org_membership import OrgMembership
from app.models.organization import Organization
from app.models.user import User


@pytest.mark.anyio
async def test_membership_api_flow(client: AsyncClient, session_factory) -> None:
    async with session_factory() as session:
        # Create Owner User & Org
        owner = User(email="owner_api@example.com", password_hash="hash", is_active=True)
        session.add(owner)
        await session.flush()

        org = Organization(name="API Org", slug="api-org")
        session.add(org)
        await session.flush()

        owner_mem = OrgMembership(user_id=owner.id, org_id=org.id, role="owner")
        session.add(owner_mem)
        await session.commit()

        token = create_access_token(owner.id)
        client.cookies.set("access_token", token)

    # 1. List memberships
    resp = await client.get(f"/orgs/{org.id}/memberships")
    assert resp.status_code == 200
    memberships = resp.json()
    assert len(memberships) == 1
    assert memberships[0]["email"] == "owner_api@example.com"
    assert memberships[0]["role"] == "owner"

    # 2. Invite a new user
    invite_resp = await client.post(
        f"/orgs/{org.id}/invites",
        json={"email": "invited_api@example.com", "role": "member"},
    )
    assert invite_resp.status_code == 201
    invite_data = invite_resp.json()
    assert invite_data["status"] == "invited"
    assert "token" in invite_data

    invite_token = invite_data["token"]

    # 3. List memberships again -> should now have 2 memberships
    list_resp = await client.get(f"/orgs/{org.id}/memberships")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 2

    # 4. Accept invite as the invited user
    accept_resp = await client.post(
        f"/orgs/{org.id}/invites/accept",
        json={"token": invite_token},
    )
    assert accept_resp.status_code == 200
    assert accept_resp.json()["status"] == "invite_accepted"

    # Get invited user ID from DB
    async with session_factory() as session:
        from sqlalchemy import select
        invited_user = await session.scalar(select(User).where(User.email == "invited_api@example.com"))
        invited_user_id = invited_user.id

    # 5. Update invited user role to admin
    patch_resp = await client.patch(
        f"/orgs/{org.id}/memberships/{invited_user_id}",
        json={"role": "admin"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["role"] == "admin"

    # 6. Remove member
    del_resp = await client.delete(
        f"/orgs/{org.id}/memberships/{invited_user_id}",
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "membership_removed"


@pytest.mark.anyio
async def test_membership_api_role_security(client: AsyncClient, session_factory) -> None:
    async with session_factory() as session:
        member_user = User(email="regular_member@example.com", password_hash="hash", is_active=True)
        session.add(member_user)
        await session.flush()

        org = Organization(name="Sec Org", slug="sec-org")
        session.add(org)
        await session.flush()

        mem = OrgMembership(user_id=member_user.id, org_id=org.id, role="member")
        session.add(mem)
        await session.commit()

        token = create_access_token(member_user.id)
        client.cookies.set("access_token", token)

    # Member tries to invite someone -> 403 Forbidden
    resp = await client.post(
        f"/orgs/{org.id}/invites",
        json={"email": "unauthorized@example.com", "role": "member"},
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_cross_tenant_membership_access(client: AsyncClient, session_factory) -> None:
    async with session_factory() as session:
        user1 = User(email="tenant1@example.com", password_hash="hash", is_active=True)
        session.add(user1)
        await session.flush()

        org1 = Organization(name="Org 1", slug="org-1")
        org2 = Organization(name="Org 2", slug="org-2")
        session.add(org1)
        session.add(org2)
        await session.flush()

        session.add(OrgMembership(user_id=user1.id, org_id=org1.id, role="owner"))
        await session.commit()

        token = create_access_token(user1.id)
        client.cookies.set("access_token", token)

    # Accessing org2 when user1 is not a member -> 403 Forbidden
    resp = await client.get(f"/orgs/{org2.id}/memberships")
    assert resp.status_code == 403

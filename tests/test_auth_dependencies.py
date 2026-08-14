import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.auth.dependencies import get_current_org, require_role
from app.auth.service import check_role_sufficient, get_user_role_in_org, verify_org_access
from app.auth.tokens import create_access_token
from app.models.org_membership import OrgMembership
from app.models.organization import Organization
from app.models.user import User


@pytest.mark.anyio
async def test_get_user_role_in_org(session_factory) -> None:
    async with session_factory() as session:
        user = User(email="user@example.com", password_hash="hash", is_active=True)
        session.add(user)
        await session.flush()

        org = Organization(name="Test Org", slug="test-org")
        session.add(org)
        await session.flush()

        membership = OrgMembership(user_id=user.id, org_id=org.id, role="admin")
        session.add(membership)
        await session.commit()

        role = await get_user_role_in_org(session, user.id, org.id)
        assert role == "admin"


@pytest.mark.anyio
async def test_get_user_role_in_org_no_membership(session_factory) -> None:
    async with session_factory() as session:
        user = User(email="user@example.com", password_hash="hash", is_active=True)
        session.add(user)
        await session.flush()

        org = Organization(name="Test Org", slug="test-org")
        session.add(org)
        await session.commit()

        role = await get_user_role_in_org(session, user.id, org.id)
        assert role is None


@pytest.mark.anyio
async def test_check_role_sufficient(session_factory) -> None:
    assert check_role_sufficient("owner", "owner") is True
    assert check_role_sufficient("owner", "admin") is True
    assert check_role_sufficient("owner", "member") is True
    assert check_role_sufficient("owner", "viewer") is True
    
    assert check_role_sufficient("admin", "admin") is True
    assert check_role_sufficient("admin", "member") is True
    assert check_role_sufficient("admin", "viewer") is True
    assert check_role_sufficient("admin", "owner") is False
    
    assert check_role_sufficient("member", "member") is True
    assert check_role_sufficient("member", "viewer") is True
    assert check_role_sufficient("member", "admin") is False
    assert check_role_sufficient("member", "owner") is False
    
    assert check_role_sufficient("viewer", "viewer") is True
    assert check_role_sufficient("viewer", "member") is False
    assert check_role_sufficient("viewer", "admin") is False
    assert check_role_sufficient("viewer", "owner") is False


@pytest.mark.anyio
async def test_verify_org_access_valid(session_factory) -> None:
    async with session_factory() as session:
        user = User(email="user@example.com", password_hash="hash", is_active=True)
        session.add(user)
        await session.flush()

        org = Organization(name="Test Org", slug="test-org")
        session.add(org)
        await session.flush()

        membership = OrgMembership(user_id=user.id, org_id=org.id, role="admin")
        session.add(membership)
        await session.commit()

        resolved_org, role = await verify_org_access(session, user.id, org.id, "viewer")
        assert resolved_org.id == org.id
        assert role == "admin"


@pytest.mark.anyio
async def test_verify_org_access_no_membership(session_factory) -> None:
    async with session_factory() as session:
        user = User(email="user@example.com", password_hash="hash", is_active=True)
        session.add(user)
        await session.flush()

        org = Organization(name="Test Org", slug="test-org")
        session.add(org)
        await session.commit()

        from app.auth.service import OrgAccessError
        with pytest.raises(OrgAccessError):
            await verify_org_access(session, user.id, org.id, "viewer")


@pytest.mark.anyio
async def test_verify_org_access_insufficient_role(session_factory) -> None:
    async with session_factory() as session:
        user = User(email="user@example.com", password_hash="hash", is_active=True)
        session.add(user)
        await session.flush()

        org = Organization(name="Test Org", slug="test-org")
        session.add(org)
        await session.flush()

        membership = OrgMembership(user_id=user.id, org_id=org.id, role="viewer")
        session.add(membership)
        await session.commit()

        from app.auth.service import OrgAccessError
        with pytest.raises(OrgAccessError):
            await verify_org_access(session, user.id, org.id, "admin")


@pytest.mark.anyio
async def test_require_role_passes(session_factory) -> None:
    # Should not raise
    require_role("owner", "owner")
    require_role("admin", "admin")
    require_role("member", "member")
    require_role("viewer", "viewer")


@pytest.mark.anyio
async def test_require_role_fails_insufficient(session_factory) -> None:
    with pytest.raises(HTTPException) as exc:
        require_role("admin", "member")
    assert exc.value.status_code == 403
    
    with pytest.raises(HTTPException) as exc:
        require_role("owner", "admin")
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_cross_org_access_blocked(session_factory) -> None:
    async with session_factory() as session:
        user = User(email="user@example.com", password_hash="hash", is_active=True)
        session.add(user)
        await session.flush()

        org1 = Organization(name="Org 1", slug="org-1")
        org2 = Organization(name="Org 2", slug="org-2")
        session.add(org1)
        session.add(org2)
        await session.flush()

        # User only belongs to org1
        membership = OrgMembership(user_id=user.id, org_id=org1.id, role="admin")
        session.add(membership)
        await session.commit()

        from app.auth.service import OrgAccessError
        # Trying to access org2 should fail
        with pytest.raises(OrgAccessError):
            await verify_org_access(session, user.id, org2.id, "viewer")

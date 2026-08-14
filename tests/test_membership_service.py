import pytest
from sqlalchemy import select

from app.auth.service import (
    InsufficientRoleError,
    invite_user_to_org,
    accept_invite,
    update_membership_role,
    remove_membership,
)
from app.models.org_membership import OrgMembership
from app.models.organization import Organization
from app.models.user import User


@pytest.mark.anyio
async def test_invite_user_to_org_creates_pending_membership(session_factory) -> None:
    async with session_factory() as session:
        # Create inviter (owner of org)
        inviter = User(email="owner@example.com", password_hash="hash", is_active=True)
        session.add(inviter)
        await session.flush()

        org = Organization(name="Test Org", slug="test-org")
        session.add(org)
        await session.flush()

        owner_membership = OrgMembership(user_id=inviter.id, org_id=org.id, role="owner")
        session.add(owner_membership)
        await session.commit()

        # Invite new user
        await invite_user_to_org(session, org.id, inviter.id, "newuser@example.com", "member")

        # Check pending membership was created for the invited user
        invited_user = await session.scalar(select(User).where(User.email == "newuser@example.com"))
        assert invited_user is not None
        
        membership = await session.scalar(
            select(OrgMembership).where(
                OrgMembership.user_id == invited_user.id,
                OrgMembership.org_id == org.id,
            )
        )
        assert membership is not None
        assert membership.role == "member"
        assert membership.accepted_at is None


@pytest.mark.anyio
async def test_invite_user_to_org_sets_invite_token_on_user(session_factory) -> None:
    async with session_factory() as session:
        inviter = User(email="owner@example.com", password_hash="hash", is_active=True)
        session.add(inviter)
        await session.flush()

        org = Organization(name="Test Org", slug="test-org")
        session.add(org)
        await session.flush()

        owner_membership = OrgMembership(user_id=inviter.id, org_id=org.id, role="owner")
        session.add(owner_membership)
        await session.commit()

        # Create the user being invited
        invited_user = User(email="newuser@example.com", password_hash="hash", is_active=True)
        session.add(invited_user)
        await session.commit()

        # Invite the user
        await invite_user_to_org(session, org.id, inviter.id, "newuser@example.com", "admin")

        await session.refresh(invited_user)
        assert invited_user.invite_token_hash is not None
        assert invited_user.invite_token_expires_at is not None
        assert invited_user.invited_to_org_id == org.id


@pytest.mark.anyio
async def test_invite_requires_owner_or_admin(session_factory) -> None:
    async with session_factory() as session:
        inviter = User(email="member@example.com", password_hash="hash", is_active=True)
        session.add(inviter)
        await session.flush()

        org = Organization(name="Test Org", slug="test-org")
        session.add(org)
        await session.flush()

        member_membership = OrgMembership(user_id=inviter.id, org_id=org.id, role="member")
        session.add(member_membership)
        await session.commit()

        with pytest.raises(InsufficientRoleError):
            await invite_user_to_org(session, org.id, inviter.id, "newuser@example.com", "member")


@pytest.mark.anyio
async def test_accept_invite_sets_accepted_at(session_factory) -> None:
    async with session_factory() as session:
        user = User(email="newuser@example.com", password_hash="hash", is_active=True)
        session.add(user)
        await session.flush()

        org = Organization(name="Test Org", slug="test-org")
        session.add(org)
        await session.flush()

        membership = OrgMembership(user_id=user.id, org_id=org.id, role="member")
        session.add(membership)
        await session.commit()

        await accept_invite(session, user.id, org.id)

        await session.refresh(membership)
        assert membership.accepted_at is not None


@pytest.mark.anyio
async def test_update_membership_role_by_owner(session_factory) -> None:
    async with session_factory() as session:
        owner = User(email="owner@example.com", password_hash="hash", is_active=True)
        member = User(email="member@example.com", password_hash="hash", is_active=True)
        session.add(owner)
        session.add(member)
        await session.flush()

        org = Organization(name="Test Org", slug="test-org")
        session.add(org)
        await session.flush()

        owner_membership = OrgMembership(user_id=owner.id, org_id=org.id, role="owner")
        member_membership = OrgMembership(user_id=member.id, org_id=org.id, role="member")
        session.add(owner_membership)
        session.add(member_membership)
        await session.commit()

        await update_membership_role(session, org.id, member.id, "admin", owner.id)

        await session.refresh(member_membership)
        assert member_membership.role == "admin"


@pytest.mark.anyio
async def test_update_membership_requires_sufficient_role(session_factory) -> None:
    async with session_factory() as session:
        member1 = User(email="member1@example.com", password_hash="hash", is_active=True)
        member2 = User(email="member2@example.com", password_hash="hash", is_active=True)
        session.add(member1)
        session.add(member2)
        await session.flush()

        org = Organization(name="Test Org", slug="test-org")
        session.add(org)
        await session.flush()

        membership1 = OrgMembership(user_id=member1.id, org_id=org.id, role="member")
        membership2 = OrgMembership(user_id=member2.id, org_id=org.id, role="viewer")
        session.add(membership1)
        session.add(membership2)
        await session.commit()

        with pytest.raises(InsufficientRoleError):
            await update_membership_role(session, org.id, member2.id, "member", member1.id)


@pytest.mark.anyio
async def test_remove_membership_by_owner(session_factory) -> None:
    async with session_factory() as session:
        owner = User(email="owner@example.com", password_hash="hash", is_active=True)
        member = User(email="member@example.com", password_hash="hash", is_active=True)
        session.add(owner)
        session.add(member)
        await session.flush()

        org = Organization(name="Test Org", slug="test-org")
        session.add(org)
        await session.flush()

        owner_membership = OrgMembership(user_id=owner.id, org_id=org.id, role="owner")
        member_membership = OrgMembership(user_id=member.id, org_id=org.id, role="member")
        session.add(owner_membership)
        session.add(member_membership)
        await session.commit()

        await remove_membership(session, org.id, member.id, owner.id)

        result = await session.scalar(
            select(OrgMembership).where(
                OrgMembership.user_id == member.id,
                OrgMembership.org_id == org.id,
            )
        )
        assert result is None


@pytest.mark.anyio
async def test_cannot_remove_last_owner(session_factory) -> None:
    async with session_factory() as session:
        owner = User(email="owner@example.com", password_hash="hash", is_active=True)
        session.add(owner)
        await session.flush()

        org = Organization(name="Test Org", slug="test-org")
        session.add(org)
        await session.flush()

        owner_membership = OrgMembership(user_id=owner.id, org_id=org.id, role="owner")
        session.add(owner_membership)
        await session.commit()

        with pytest.raises(ValueError):  # Cannot remove last owner
            await remove_membership(session, org.id, owner.id, owner.id)

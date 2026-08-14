from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.auth import service
from app.auth.service import (
    EmailAlreadyRegisteredError,
    issue_verification,
    register_user,
    verify_email_token,
)
from app.models.org_membership import OrgMembership
from app.models.organization import Organization
from app.models.user import User
from app.security import verify_password


@pytest.mark.anyio
async def test_register_creates_user_org_and_owner(session_factory) -> None:
    async with session_factory() as session:
        user = await register_user(session, "alice@example.com", "password123")

        assert user.email == "alice@example.com"
        assert verify_password("password123", user.password_hash)
        assert user.is_active is True

        org = (
            await session.execute(
                select(Organization).where(Organization.slug == "alice")
            )
        ).scalar_one()
        membership = (
            await session.execute(
                select(OrgMembership).where(
                    OrgMembership.user_id == user.id,
                    OrgMembership.org_id == org.id,
                )
            )
        ).scalar_one()
        assert membership.role == "owner"
        assert membership.accepted_at is not None


@pytest.mark.anyio
async def test_register_duplicate_email_raises(session_factory) -> None:
    async with session_factory() as session:
        await register_user(session, "bob@example.com", "password123")
        with pytest.raises(EmailAlreadyRegisteredError):
            await register_user(session, "BOB@example.com", "password123")


@pytest.mark.anyio
async def test_issue_verification_sets_hashed_token(session_factory) -> None:
    async with session_factory() as session:
        user = await register_user(session, "carol@example.com", "password123")
        token = await issue_verification(session, user)

        assert token
        await session.refresh(user)
        assert user.email_verification_token_hash is not None
        assert user.email_verification_token_hash != token
        assert user.email_verification_token_expires_at is not None
        assert user.email_verification_token_expires_at > datetime.now(timezone.utc)


@pytest.mark.anyio
async def test_verify_email_token_flow(session_factory) -> None:
    async with session_factory() as session:
        user = await register_user(session, "dave@example.com", "password123")
        token = await issue_verification(session, user)

        verified = await verify_email_token(session, token)
        assert verified is not None
        assert verified.id == user.id
        assert verified.email_verified_at is not None

        # token is single-use
        assert await verify_email_token(session, token) is None


@pytest.mark.anyio
async def test_verify_email_token_rejects_unknown(session_factory) -> None:
    async with session_factory() as session:
        assert await verify_email_token(session, "not-a-real-token") is None


@pytest.mark.anyio
async def test_verify_email_token_rejects_expired(session_factory) -> None:
    async with session_factory() as session:
        user = await register_user(session, "erin@example.com", "password123")
        token = await issue_verification(session, user)
        user.email_verification_token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()

        assert await verify_email_token(session, token) is None

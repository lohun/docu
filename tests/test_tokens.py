from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from sqlalchemy import select

from app.auth import tokens as t
from app.config import get_settings
from app.models.refresh_token import RefreshToken
from app.models.user import User


def test_access_token_roundtrip() -> None:
    token = t.create_access_token(42)
    payload = t.decode_access_token(token)
    assert payload["sub"] == "42"
    assert payload["type"] == "access"


def test_access_token_rejects_wrong_type() -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    token = pyjwt.encode(
        {"sub": "42", "type": "refresh", "iat": now, "exp": now + timedelta(minutes=5)},
        settings.jwt_secret_key,
        algorithm=t.ALGORITHM,
    )
    with pytest.raises(pyjwt.InvalidTokenError):
        t.decode_access_token(token)


def test_access_token_rejects_expired() -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    token = pyjwt.encode(
        {"sub": "42", "type": "access", "iat": now - timedelta(minutes=30), "exp": now - timedelta(minutes=1)},
        settings.jwt_secret_key,
        algorithm=t.ALGORITHM,
    )
    with pytest.raises(pyjwt.ExpiredSignatureError):
        t.decode_access_token(token)


def test_access_token_rejects_wrong_secret() -> None:
    now = datetime.now(timezone.utc)
    token = pyjwt.encode(
        {"sub": "42", "type": "access", "iat": now, "exp": now + timedelta(minutes=5)},
        "a-different-secret",
        algorithm=t.ALGORITHM,
    )
    with pytest.raises(pyjwt.InvalidSignatureError):
        t.decode_access_token(token)


def test_refresh_tokens_are_unique_and_hashable() -> None:
    a = t.generate_refresh_token()
    b = t.generate_refresh_token()
    assert a != b
    assert len(t.hash_refresh_token(a)) == 64


@pytest.mark.anyio
async def test_issue_and_rotate(session_factory) -> None:
    async with session_factory() as session:
        user = User(email="issue@example.com", password_hash="x")
        session.add(user)
        await session.commit()
        user_id = user.id

    token = await _issue(session_factory, user_id)
    token_hash = t.hash_refresh_token(token)

    async with session_factory() as session:
        record = (
            await session.execute(
                select(RefreshToken).where(RefreshToken.user_id == user_id)
            )
        ).scalar_one()
        assert record.token_hash == token_hash
        assert record.revoked_at is None
        assert record.expires_at > datetime.now(timezone.utc)

    result = await _rotate(session_factory, token)
    assert result is not None
    old_user_id, new_token = result
    assert old_user_id == user_id
    assert new_token != token

    async with session_factory() as session:
        records = (
            await session.execute(
                select(RefreshToken)
                .where(RefreshToken.user_id == user_id)
                .order_by(RefreshToken.id)
            )
        ).scalars().all()
        assert records[0].revoked_at is not None
        assert records[1].revoked_at is None
        assert records[1].token_hash == t.hash_refresh_token(new_token)

    assert await _rotate(session_factory, token) is None


@pytest.mark.anyio
async def test_revoke_prevents_rotation(session_factory) -> None:
    async with session_factory() as session:
        user = User(email="revoke@example.com", password_hash="x")
        session.add(user)
        await session.commit()
        user_id = user.id

    token = await _issue(session_factory, user_id)
    await _revoke(session_factory, token)

    async with session_factory() as session:
        record = (
            await session.execute(
                select(RefreshToken).where(RefreshToken.user_id == user_id)
            )
        ).scalar_one()
        assert record.revoked_at is not None

    assert await _rotate(session_factory, token) is None


async def _issue(session_factory, user_id: int) -> str:
    async with session_factory() as session:
        return await t.issue_refresh_token(session, user_id)


async def _rotate(session_factory, token: str):
    async with session_factory() as session:
        return await t.rotate_refresh_token(session, token)


async def _revoke(session_factory, token: str) -> None:
    async with session_factory() as session:
        await t.revoke_refresh_token(session, token)

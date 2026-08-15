from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from sqlalchemy import select

from app.auth import tokens as t
from app.config import get_settings
from app.models.refresh_token import RefreshToken
from app.models.user import User


def test_access_token_roundtrip() -> None:
    settings = get_settings()
    token = t.create_access_token(42)
    payload = t.decode_access_token(token)
    assert payload["sub"] == "42"
    assert payload["type"] == "access"
    assert payload["ver"] == 1
    assert payload["iss"] == settings.jwt_issuer
    assert payload["aud"] == settings.jwt_audience


def test_access_token_carries_token_version() -> None:
    token = t.create_access_token(7, token_version=4)
    payload = t.decode_access_token(token)
    assert payload["ver"] == 4


def _encode_payload(payload: dict, secret: str | None = None) -> str:
    if secret is None:
        secret = get_settings().jwt_secret_key
    return pyjwt.encode(payload, secret, algorithm=t.ALGORITHM)


def _valid_payload(**overrides: object) -> dict:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "42",
        "type": "access",
        "ver": 1,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    payload.update(overrides)
    return payload


def test_access_token_rejects_wrong_type() -> None:
    token = _encode_payload(_valid_payload(type="refresh"))
    with pytest.raises(pyjwt.InvalidTokenError):
        t.decode_access_token(token)


def test_access_token_rejects_expired() -> None:
    now = datetime.now(timezone.utc)
    token = _encode_payload(
        _valid_payload(iat=now - timedelta(minutes=30), exp=now - timedelta(minutes=1))
    )
    with pytest.raises(pyjwt.ExpiredSignatureError):
        t.decode_access_token(token)


def test_access_token_rejects_wrong_secret() -> None:
    token = _encode_payload(_valid_payload(), secret="a-different-secret")
    with pytest.raises(pyjwt.InvalidSignatureError):
        t.decode_access_token(token)


def test_access_token_rejects_wrong_issuer() -> None:
    token = _encode_payload(_valid_payload(iss="evil-issuer"))
    with pytest.raises(pyjwt.InvalidIssuerError):
        t.decode_access_token(token)


def test_access_token_rejects_wrong_audience() -> None:
    token = _encode_payload(_valid_payload(aud="evil-audience"))
    with pytest.raises(pyjwt.InvalidAudienceError):
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

    # Replay of the rotated token is reuse => whole family revoked.
    with pytest.raises(t.RefreshTokenReuseError):
        await _rotate(session_factory, token)


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

    # Replaying an explicitly revoked token is treated as reuse.
    with pytest.raises(t.RefreshTokenReuseError):
        await _rotate(session_factory, token)


@pytest.mark.anyio
async def test_rotation_preserves_family_id(session_factory) -> None:
    async with session_factory() as session:
        user = User(email="fam@example.com", password_hash="x")
        session.add(user)
        await session.commit()
        user_id = user.id

    token = await _issue(session_factory, user_id)

    async with session_factory() as session:
        first = (
            await session.execute(select(RefreshToken).where(RefreshToken.user_id == user_id))
        ).scalar_one()
        family_id = first.family_id
        assert family_id

    _user_id, new_token = await _rotate(session_factory, token)
    assert _user_id == user_id

    async with session_factory() as session:
        child = (
            await session.execute(
                select(RefreshToken).where(RefreshToken.token_hash == t.hash_refresh_token(new_token))
            )
        ).scalar_one()
        assert child.family_id == family_id


@pytest.mark.anyio
async def test_reuse_of_rotated_token_revokes_whole_family(session_factory) -> None:
    async with session_factory() as session:
        user = User(email="reuse@example.com", password_hash="x")
        session.add(user)
        await session.commit()
        user_id = user.id

    token = await _issue(session_factory, user_id)
    _user_id, new_token = await _rotate(session_factory, token)

    async with session_factory() as session:
        family_id = (
            await session.execute(
                select(RefreshToken).where(RefreshToken.token_hash == t.hash_refresh_token(new_token))
            )
        ).scalar_one().family_id

    # Replaying the already-rotated token is theft => whole family revoked.
    with pytest.raises(t.RefreshTokenReuseError):
        await _rotate(session_factory, token)

    async with session_factory() as session:
        records = (
            await session.execute(
                select(RefreshToken).where(RefreshToken.family_id == family_id)
            )
        ).scalars().all()
        assert len(records) == 2
        assert all(r.revoked_at is not None for r in records)

    # The child token the attacker might have captured is revoked and cannot
    # be rotated either (its replay is itself another reuse event).
    with pytest.raises(t.RefreshTokenReuseError):
        await _rotate(session_factory, new_token)


async def _issue(session_factory, user_id: int) -> str:
    async with session_factory() as session:
        return await t.issue_refresh_token(session, user_id)


async def _rotate(session_factory, token: str):
    async with session_factory() as session:
        return await t.rotate_refresh_token(session, token)


async def _revoke(session_factory, token: str) -> None:
    async with session_factory() as session:
        await t.revoke_refresh_token(session, token)

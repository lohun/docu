import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.refresh_token import RefreshToken

ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"


class RefreshTokenReuseError(Exception):
    """Raised when a previously rotated (revoked) refresh token is replayed.

    The whole session family is revoked before this propagates so a stolen token
    can never be raced against a legitimate rotation to mint a usable child.
    """


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: int, token_version: int = 1) -> str:
    settings = get_settings()
    now = _now()
    payload = {
        "sub": str(user_id),
        "type": ACCESS_TOKEN_TYPE,
        "ver": token_version,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[ALGORITHM],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise jwt.InvalidTokenError("token is not an access token")
    return payload


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_family_id() -> str:
    return secrets.token_urlsafe(32)


async def issue_refresh_token(
    session: AsyncSession, user_id: int, family_id: str | None = None
) -> str:
    settings = get_settings()
    token = generate_refresh_token()
    record = RefreshToken(
        user_id=user_id,
        family_id=family_id or generate_family_id(),
        token_hash=hash_refresh_token(token),
        expires_at=_now() + timedelta(days=settings.refresh_token_expire_days),
    )
    session.add(record)
    await session.commit()
    return token


async def _find_token(session: AsyncSession, token: str) -> RefreshToken | None:
    result = await session.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(token),
            RefreshToken.expires_at > _now(),
        )
    )
    return result.scalar_one_or_none()


async def revoke_refresh_family(session: AsyncSession, family_id: str) -> None:
    """Revoke every still-active token in a session family."""
    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=_now())
    )
    await session.commit()


async def rotate_refresh_token(session: AsyncSession, token: str) -> tuple[int, str] | None:
    record = await _find_token(session, token)
    if record is None:
        return None
    if record.revoked_at is not None:
        # A revoked token is being presented again -> likely theft. Kill the
        # entire lineage and force a re-login.
        await revoke_refresh_family(session, record.family_id)
        raise RefreshTokenReuseError()
    settings = get_settings()
    new_token = generate_refresh_token()
    record.revoked_at = _now()
    session.add(
        RefreshToken(
            user_id=record.user_id,
            family_id=record.family_id,
            token_hash=hash_refresh_token(new_token),
            expires_at=_now() + timedelta(days=settings.refresh_token_expire_days),
        )
    )
    await session.commit()
    return record.user_id, new_token


async def revoke_refresh_token(session: AsyncSession, token: str) -> None:
    record = await _find_token(session, token)
    if record is not None:
        record.revoked_at = _now()
        await session.commit()
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.refresh_token import RefreshToken

ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    now = _now()
    payload = {
        "sub": str(user_id),
        "type": ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[ALGORITHM])
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise jwt.InvalidTokenError("token is not an access token")
    return payload


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_refresh_token(session: AsyncSession, user_id: int) -> str:
    settings = get_settings()
    token = generate_refresh_token()
    record = RefreshToken(
        user_id=user_id,
        token_hash=hash_refresh_token(token),
        expires_at=_now() + timedelta(days=settings.refresh_token_expire_days),
    )
    session.add(record)
    await session.commit()
    return token


async def _get_active_token(session: AsyncSession, token: str) -> RefreshToken | None:
    result = await session.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(token),
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > _now(),
        )
    )
    return result.scalar_one_or_none()


async def rotate_refresh_token(session: AsyncSession, token: str) -> tuple[int, str] | None:
    record = await _get_active_token(session, token)
    if record is None:
        return None
    settings = get_settings()
    new_token = generate_refresh_token()
    record.revoked_at = _now()
    session.add(
        RefreshToken(
            user_id=record.user_id,
            token_hash=hash_refresh_token(new_token),
            expires_at=_now() + timedelta(days=settings.refresh_token_expire_days),
        )
    )
    await session.commit()
    return record.user_id, new_token


async def revoke_refresh_token(session: AsyncSession, token: str) -> None:
    record = await _get_active_token(session, token)
    if record is not None:
        record.revoked_at = _now()
        await session.commit()

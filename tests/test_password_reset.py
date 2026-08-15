from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.auth.service import request_password_reset, reset_password
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.security import verify_password


@pytest.mark.anyio
async def test_request_password_reset_stores_hashed_token(session_factory, monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_send(to: str, reset_link: str) -> None:
        captured["link"] = reset_link

    monkeypatch.setattr("app.auth.service.send_password_reset_email", fake_send)

    async with session_factory() as session:
        from app.auth.service import register_user

        await register_user(session, "alice@example.com", "old-password")
        await request_password_reset(session, "alice@example.com")

        user = (
            await session.execute(select(User).where(User.email == "alice@example.com"))
        ).scalar_one()
        assert user.password_reset_token_hash is not None
        assert user.password_reset_token_expires_at is not None

    token = parse_qs(urlparse(captured["link"]).query)["token"][0]
    assert token
    assert user.password_reset_token_hash != token


@pytest.mark.anyio
async def test_request_password_reset_unknown_email_is_noop(session_factory, monkeypatch) -> None:
    called: list[str] = []

    def fake_send(to: str, reset_link: str) -> None:
        called.append(to)

    monkeypatch.setattr("app.auth.service.send_password_reset_email", fake_send)

    async with session_factory() as session:
        await request_password_reset(session, "ghost@example.com")
    assert called == []


@pytest.mark.anyio
async def test_reset_password_updates_hash_and_is_single_use(session_factory, monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_send(to: str, reset_link: str) -> None:
        captured["link"] = reset_link

    monkeypatch.setattr("app.auth.service.send_password_reset_email", fake_send)

    async with session_factory() as session:
        from app.auth.service import register_user

        user = await register_user(session, "bob@example.com", "old-password")
        await request_password_reset(session, "bob@example.com")
        original_version = user.token_version

    token = parse_qs(urlparse(captured["link"]).query)["token"][0]

    async with session_factory() as session:
        updated = await reset_password(session, token, "new-password")
        assert updated is not None
        await session.refresh(updated)
        assert verify_password("new-password", updated.password_hash)
        assert not verify_password("old-password", updated.password_hash)
        assert updated.password_reset_token_hash is None
        assert updated.token_version == original_version + 1

        assert await reset_password(session, token, "another-password") is None


@pytest.mark.anyio
async def test_reset_password_rejects_unknown_token(session_factory) -> None:
    async with session_factory() as session:
        assert await reset_password(session, "not-a-real-token", "new-password") is None


@pytest.mark.anyio
async def test_reset_password_rejects_expired_token(session_factory, monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_send(to: str, reset_link: str) -> None:
        captured["link"] = reset_link

    monkeypatch.setattr("app.auth.service.send_password_reset_email", fake_send)

    async with session_factory() as session:
        from app.auth.service import register_user

        user = await register_user(session, "carol@example.com", "old-password")
        await request_password_reset(session, "carol@example.com")
        user.password_reset_token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()

    token = parse_qs(urlparse(captured["link"]).query)["token"][0]

    async with session_factory() as session:
        assert await reset_password(session, token, "new-password") is None


@pytest.mark.anyio
async def test_reset_password_revokes_refresh_tokens(session_factory, monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_send(to: str, reset_link: str) -> None:
        captured["link"] = reset_link

    monkeypatch.setattr("app.auth.service.send_password_reset_email", fake_send)

    from app.auth.tokens import issue_refresh_token

    async with session_factory() as session:
        from app.auth.service import register_user

        user = await register_user(session, "dave@example.com", "old-password")
        await issue_refresh_token(session, user.id)
        await request_password_reset(session, "dave@example.com")

    token = parse_qs(urlparse(captured["link"]).query)["token"][0]

    async with session_factory() as session:
        await reset_password(session, token, "new-password")

    async with session_factory() as session:
        count = (
            await session.execute(select(RefreshToken.id).where(RefreshToken.user_id == user.id))
        ).scalars().all()
        assert count == []

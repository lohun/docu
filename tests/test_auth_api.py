from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.models.user import User


@pytest.mark.anyio
async def test_register_and_verify_email_api(client, session_factory, monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_send(to: str, verification_link: str) -> None:
        captured["link"] = verification_link

    monkeypatch.setattr("app.auth.service.send_verification_email", fake_send)

    resp = await client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "password123"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["message"] == "verification email sent"
    assert body["user"]["email"] == "alice@example.com"

    link = captured["link"]
    token = parse_qs(urlparse(link).query)["token"][0]

    verify_resp = await client.post("/auth/verify-email", json={"token": token})
    assert verify_resp.status_code == 200
    assert verify_resp.json() == {"status": "verified"}

    async with session_factory() as session:
        user = (
            await session.execute(select(User).where(User.email == "alice@example.com"))
        ).scalar_one()
        assert user.email_verified_at is not None
        assert user.email_verification_token_hash is None


@pytest.mark.anyio
async def test_register_duplicate_email_returns_409(client, session_factory) -> None:
    async with session_factory() as session:
        from app.auth.service import register_user

        await register_user(session, "bob@example.com", "password123")

    resp = await client.post(
        "/auth/register",
        json={"email": "bob@example.com", "password": "password123"},
    )
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_register_short_password_returns_422(client) -> None:
    resp = await client.post(
        "/auth/register",
        json={"email": "carol@example.com", "password": "short"},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_register_invalid_email_returns_422(client) -> None:
    resp = await client.post(
        "/auth/register",
        json={"email": "not-an-email", "password": "password123"},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_verify_email_invalid_token_returns_400(client) -> None:
    resp = await client.post("/auth/verify-email", json={"token": "garbage"})
    assert resp.status_code == 400


async def _register_verified(client, session_factory, email: str, password: str = "password123") -> None:
    from app.auth.service import issue_verification, register_user

    async with session_factory() as session:
        user = await register_user(session, email, password)
        await issue_verification(session, user)
        user.email_verified_at = datetime.now(timezone.utc)
        await session.commit()


@pytest.mark.anyio
async def test_login_success_sets_refresh_cookie(client, session_factory) -> None:
    await _register_verified(client, session_factory, "alice@example.com")

    resp = await client.post(
        "/auth/login",
        json={"email": "alice@example.com", "password": "password123"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == "alice@example.com"

    cookie = resp.cookies.get("refresh_token")
    assert cookie
    # access token must be in the access_token cookie, not the body
    access_cookie = resp.cookies.get("access_token")
    assert access_cookie


@pytest.mark.anyio
async def test_login_wrong_password_returns_401(client, session_factory) -> None:
    await _register_verified(client, session_factory, "bob@example.com")
    resp = await client.post(
        "/auth/login",
        json={"email": "bob@example.com", "password": "wrong-password"},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_login_unknown_email_returns_401(client) -> None:
    resp = await client.post(
        "/auth/login",
        json={"email": "ghost@example.com", "password": "whatever123"},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_login_unverified_email_returns_403(client, session_factory) -> None:
    from app.auth.service import register_user

    async with session_factory() as session:
        await register_user(session, "carol@example.com", "password123")

    resp = await client.post(
        "/auth/login",
        json={"email": "carol@example.com", "password": "password123"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "email not verified"


@pytest.mark.anyio
async def test_login_inactive_user_returns_403(client, session_factory) -> None:
    from app.auth.service import issue_verification, register_user

    async with session_factory() as session:
        user = await register_user(session, "dave@example.com", "password123")
        await issue_verification(session, user)
        user.email_verified_at = datetime.now(timezone.utc)
        user.is_active = False
        await session.commit()

    resp = await client.post(
        "/auth/login",
        json={"email": "dave@example.com", "password": "password123"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "account disabled"


@pytest.mark.anyio
async def test_login_rate_limited_after_five_attempts(client, session_factory) -> None:
    await _register_verified(client, session_factory, "erin@example.com")
    payload = {"email": "erin@example.com", "password": "password123"}

    statuses = []
    for _ in range(6):
        resp = await client.post("/auth/login", json=payload)
        statuses.append(resp.status_code)
    assert statuses == [200, 200, 200, 200, 200, 429]


@pytest.mark.anyio
async def test_logout_revokes_refresh_token(client, session_factory) -> None:
    await _register_verified(client, session_factory, "frank@example.com")
    login_resp = await client.post(
        "/auth/login",
        json={"email": "frank@example.com", "password": "password123"},
    )
    assert login_resp.status_code == 200

    logout_resp = await client.post("/auth/logout")
    assert logout_resp.status_code == 200
    assert logout_resp.json() == {"status": "logged_out"}
    assert client.cookies.get("refresh_token") is None


@pytest.mark.anyio
async def test_refresh_rotates_token(client, session_factory) -> None:
    await _register_verified(client, session_factory, "grace@example.com")
    login_resp = await client.post(
        "/auth/login",
        json={"email": "grace@example.com", "password": "password123"},
    )
    old_refresh = login_resp.cookies.get("refresh_token")
    assert old_refresh

    refresh_resp = await client.post("/auth/refresh")
    assert refresh_resp.status_code == 200
    body = refresh_resp.json()
    assert body["user"]["email"] == "grace@example.com"
    assert client.cookies.get("refresh_token") != old_refresh


@pytest.mark.anyio
async def test_refresh_rejects_old_token_after_rotation(client, session_factory) -> None:
    await _register_verified(client, session_factory, "heidi@example.com")
    login_resp = await client.post(
        "/auth/login",
        json={"email": "heidi@example.com", "password": "password123"},
    )
    old_refresh = login_resp.cookies.get("refresh_token")

    first = await client.post("/auth/refresh")
    assert first.status_code == 200

    resp = await client.post("/auth/refresh", headers={"Cookie": f"refresh_token={old_refresh}"})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_refresh_missing_cookie_returns_401(client) -> None:
    resp = await client.post("/auth/refresh")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "missing refresh token"


@pytest.mark.anyio
async def test_refresh_garbage_cookie_returns_401(client) -> None:
    resp = await client.post("/auth/refresh", headers={"Cookie": "refresh_token=garbage"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid or expired refresh token"


from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from tests.test_auth_api import _register_verified


@pytest.mark.anyio
async def test_password_reset_api_flow(client, session_factory, monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_send(to: str, reset_link: str) -> None:
        captured["link"] = reset_link

    monkeypatch.setattr("app.auth.service.send_password_reset_email", fake_send)

    await _register_verified(client, session_factory, "alice@example.com")

    req = await client.post(
        "/auth/password-reset/request",
        json={"email": "alice@example.com"},
    )
    assert req.status_code == 200
    assert req.json() == {"status": "password_reset_email_sent"}

    token = parse_qs(urlparse(captured["link"]).query)["token"][0]

    confirm = await client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "fresh-password"},
    )
    assert confirm.status_code == 200
    assert confirm.json() == {"status": "password_updated"}

    old = await client.post(
        "/auth/login",
        json={"email": "alice@example.com", "password": "password123"},
    )
    assert old.status_code == 401

    new_login = await client.post(
        "/auth/login",
        json={"email": "alice@example.com", "password": "fresh-password"},
    )
    assert new_login.status_code == 200


@pytest.mark.anyio
async def test_password_reset_request_unknown_email_returns_200(client) -> None:
    resp = await client.post(
        "/auth/password-reset/request",
        json={"email": "ghost@example.com"},
    )
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_password_reset_confirm_bad_token_returns_400(client) -> None:
    resp = await client.post(
        "/auth/password-reset/confirm",
        json={"token": "garbage", "new_password": "fresh-password"},
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_password_reset_confirm_short_password_returns_422(client) -> None:
    resp = await client.post(
        "/auth/password-reset/confirm",
        json={"token": "whatever", "new_password": "short"},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_password_reset_rotates_sessions_out(client, session_factory, monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_send(to: str, reset_link: str) -> None:
        captured["link"] = reset_link

    monkeypatch.setattr("app.auth.service.send_password_reset_email", fake_send)

    await _register_verified(client, session_factory, "bob@example.com")
    login = await client.post(
        "/auth/login",
        json={"email": "bob@example.com", "password": "password123"},
    )
    assert login.status_code == 200
    old_refresh = login.cookies.get("refresh_token")

    await client.post(
        "/auth/password-reset/request",
        json={"email": "bob@example.com"},
    )
    token = parse_qs(urlparse(captured["link"]).query)["token"][0]
    await client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "changed-password"},
    )

    refresh_resp = await client.post("/auth/refresh", headers={"Cookie": f"refresh_token={old_refresh}"})
    assert refresh_resp.status_code == 401

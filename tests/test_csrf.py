from collections.abc import AsyncIterator

import httpx
import pytest

from app.csrf import csrf_cookie_name, validate_csrf_value
from app.db import get_session
from app.main import create_app
from app.rate_limit import limiter


@pytest.fixture()
async def plain_client(session_factory) -> AsyncIterator[httpx.AsyncClient]:
    """A client that does NOT auto-send the CSRF header, like an attacker.

    Lets us assert the middleware rejects requests that skip the double-submit
    handshake. Uses fresh limiter state so no cross-test rate-limit bleed.
    """
    app = create_app()

    async def override_get_session() -> AsyncIterator:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=True) as c:
        yield c
    app.dependency_overrides.clear()
    limiter.reset()


@pytest.mark.anyio
async def test_csrf_handshake_issues_signed_cookie(plain_client) -> None:
    resp = await plain_client.get("/auth/csrf")
    assert resp.status_code == 200
    cookie = plain_client.cookies.get(csrf_cookie_name())
    assert cookie
    assert validate_csrf_value(cookie)


@pytest.mark.anyio
async def test_state_changing_request_without_csrf_is_rejected(plain_client) -> None:
    resp = await plain_client.post(
        "/auth/register",
        json={"email": "noc@example.com", "password": "password123"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "invalid or missing CSRF token"


@pytest.mark.anyio
async def test_state_changing_request_with_csrf_header_passes(plain_client) -> None:
    await plain_client.get("/auth/csrf")
    csrf = plain_client.cookies.get(csrf_cookie_name())

    resp = await plain_client.post(
        "/auth/register",
        headers={"X-CSRF-Token": csrf},
        json={"email": "yes@example.com", "password": "password123"},
    )
    assert resp.status_code == 201


@pytest.mark.anyio
async def test_tampered_csrf_header_is_rejected(plain_client) -> None:
    await plain_client.get("/auth/csrf")
    await plain_client.get("/auth/csrf")  # re-issued cookie
    stale = plain_client.cookies.get(csrf_cookie_name())

    resp = await plain_client.post(
        "/auth/register",
        headers={"X-CSRF-Token": stale + "tampered"},
        json={"email": "tamper@example.com", "password": "password123"},
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_cookie_value_is_signed_not_plaintext(plain_client) -> None:
    await plain_client.get("/auth/csrf")
    cookie = plain_client.cookies.get(csrf_cookie_name())
    # An attacker who can inject their own bare value must still fail.
    forged = "attacker-controlled-token.not-a-valid-mac"
    assert validate_csrf_value(forged) is False
    assert cookie != forged
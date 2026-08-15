import pytest

from sqlalchemy import select

from app.auth.service import issue_verification, register_user
from app.csrf import csrf_cookie_name
from app.models.refresh_token import RefreshToken
from app.models.user import User


def _crafted_headers(client, refresh_token: str) -> dict[str, str]:
    """A stolen-token replay: attacker still has the CSRF cookie value (Sent
    cross-site by SameSite=None) and a captured refresh token."""
    csrf = client.cookies.get(csrf_cookie_name())
    return {
        "Cookie": f"refresh_token={refresh_token}; {csrf_cookie_name()}={csrf}",
        "X-CSRF-Token": csrf,
    }


async def _register_verified(session_factory, email: str) -> None:
    async with session_factory() as session:
        user = await register_user(session, email, "password123")
        await issue_verification(session, user)
        from datetime import datetime, timezone

        user.email_verified_at = datetime.now(timezone.utc)
        await session.commit()


@pytest.mark.anyio
async def test_replaying_old_token_revokes_family_via_api(client, session_factory) -> None:
    await _register_verified(session_factory, "reuse@example.com")
    login_resp = await client.post(
        "/auth/login",
        json={"email": "reuse@example.com", "password": "password123"},
    )
    assert login_resp.status_code == 200
    old_refresh = login_resp.cookies.get("refresh_token")

    first = await client.post("/auth/refresh")
    assert first.status_code == 200
    child_refresh = client.cookies.get("refresh_token")
    assert child_refresh != old_refresh

    # Attacker replays the captured pre-rotation token.
    replay = await client.post("/auth/refresh", headers=_crafted_headers(client, old_refresh))
    assert replay.status_code == 401
    assert "reuse" in replay.json()["detail"]

    # The whole family is now revoked: the child the attacker may have grabbed is dead.
    child_attempt = await client.post("/auth/refresh", headers=_crafted_headers(client, child_refresh))
    assert child_attempt.status_code == 401

    async with session_factory() as session:
        user = (
            await session.execute(select(User).where(User.email == "reuse@example.com"))
        ).scalar_one()
        records = (
            await session.execute(
                select(RefreshToken).where(RefreshToken.user_id == user.id)
            )
        ).scalars().all()
        assert records
        assert all(r.revoked_at is not None for r in records)
import logging

import httpx
import pytest
from fastapi import FastAPI

from app.auth.tokens import create_access_token
from app.config import get_settings
from app.logging_conf import RedactFilter
from app.main import create_app
from app.models.org_membership import OrgMembership
from app.models.organization import Organization
from app.models.source import Source
from app.models.user import User
from app.scheduler import pipeline as pipeline_module


def _make_production_app() -> FastAPI:
    app = create_app()

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret-traceback /etc/docversion/.env nvapi-LEAKKEY1234567890")

    return app


@pytest.mark.anyio
async def test_error_responses_do_not_leak_stack_traces_in_production(monkeypatch) -> None:
    monkeypatch.setenv("DOCVERSION_ENVIRONMENT", "production")
    monkeypatch.setenv("DOCVERSION_DEBUG", "false")
    monkeypatch.setenv("DOCVERSION_JWT_SECRET_KEY", "a-strong-production-secret-that-is-long-enough")
    get_settings.cache_clear()
    try:
        app = _make_production_app()
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/boom")
        assert resp.status_code == 500
        assert resp.json() == {"detail": "internal server error"}
        assert "secret-traceback" not in resp.text
        assert "/etc/docversion" not in resp.text
        assert "nvapi-LEAKKEY1234567890" not in resp.text
    finally:
        get_settings.cache_clear()


@pytest.mark.anyio
async def test_api_responses_never_contain_nvidia_key(client, monkeypatch) -> None:
    monkeypatch.setenv("DOCVERSION_NVIDIA_API_KEY", "nvapi-SUPERSECRETKEY9876543210")
    get_settings.cache_clear()
    try:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "nvapi-SUPERSECRETKEY9876543210" not in resp.text
        assert "nvapi-" not in resp.text
    finally:
        get_settings.cache_clear()


def test_logs_redact_secrets() -> None:
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        "/x",
        1,
        "auth failed password=supersecret123 and nvapi-ABCDEFGHIJKLMNOP",
        (),
        None,
    )
    RedactFilter().filter(record)
    message = record.getMessage()
    assert "supersecret123" not in message
    assert "nvapi-ABCDEFGHIJKLMNOP" not in message


@pytest.mark.anyio
async def test_run_now_rate_limit_per_org_not_global(
    client, session_factory, monkeypatch
) -> None:
    async def noop(session, source_id, **kwargs):
        from app.models.run_log import RunLog

        session.add(RunLog(source_id=source_id, outcome="success"))
        await session.commit()
        return None

    monkeypatch.setattr(pipeline_module, "trigger_pipeline_run", noop)

    async with session_factory() as session:
        u1 = User(email="rl1@example.com", password_hash="hash", is_active=True)
        session.add(u1)
        await session.flush()

        org_a = Organization(name="RL A", slug="rl-a")
        org_b = Organization(name="RL B", slug="rl-b")
        session.add(org_a)
        session.add(org_b)
        await session.flush()

        session.add(OrgMembership(user_id=u1.id, org_id=org_a.id, role="member"))
        session.add(OrgMembership(user_id=u1.id, org_id=org_b.id, role="member"))

        sa = Source(org_id=org_a.id, name="A", type="openapi", target_url="https://example.com/a")
        sb = Source(org_id=org_b.id, name="B", type="openapi", target_url="https://example.com/b")
        session.add(sa)
        session.add(sb)
        await session.commit()
        sa_id, sb_id = sa.id, sb.id
        org_a_id, org_b_id = org_a.id, org_b.id

    token = create_access_token(u1.id)
    client.cookies.set("access_token", token)

    statuses_a = []
    for _ in range(10):
        resp = await client.post(
            f"/orgs/{org_a_id}/sources/{sa_id}/run-now"
        )
        statuses_a.append(resp.status_code)
    assert statuses_a == [202] * 10

    exhausted = await client.post(
        f"/orgs/{org_a_id}/sources/{sa_id}/run-now"
    )
    assert exhausted.status_code == 429

    # Org B is not blocked by Org A's consumption.
    resp_b = await client.post(
        f"/orgs/{org_b_id}/sources/{sb_id}/run-now"
    )
    assert resp_b.status_code == 202

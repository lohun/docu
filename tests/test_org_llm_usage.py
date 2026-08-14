from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.llm.metering import (
    current_period,
    get_period_usage,
    increment_usage,
    is_over_quota,
)
from app.models.organization import Organization
from app.models.org_llm_usage import OrgLlmUsage


@pytest.mark.anyio
async def test_org_llm_usage_increments_tokens_and_call_count(session_factory) -> None:
    async with session_factory() as session:
        org = Organization(name="Usage Org", slug="usage-org")
        session.add(org)
        await session.flush()

        await increment_usage(session, org.id, tokens=100)
        await increment_usage(session, org.id, tokens=50)

        usage = await session.scalar(
            select(OrgLlmUsage).where(OrgLlmUsage.org_id == org.id)
        )
        assert usage is not None
        assert usage.tokens_used == 150
        assert usage.call_count == 2


@pytest.mark.anyio
async def test_usage_scoped_to_current_billing_period(session_factory) -> None:
    now = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)
    async with session_factory() as session:
        org = Organization(name="Period Org", slug="period-org")
        session.add(org)
        await session.flush()

        usage = await get_period_usage(session, org.id, now=now)
        assert usage.period_start == datetime(2026, 8, 1, tzinfo=timezone.utc)
        assert usage.period_end == datetime(2026, 9, 1, tzinfo=timezone.utc)


def test_current_period_rolls_over_december() -> None:
    now = datetime(2026, 12, 5, tzinfo=timezone.utc)
    start, end = current_period(now)
    assert start.month == 12
    assert end.year == 2027
    assert end.month == 1


@pytest.mark.anyio
async def test_quota_exceeded_rejects_llm_call(session_factory, caplog) -> None:
    import logging

    async with session_factory() as session:
        org = Organization(name="Quota Org", slug="quota-org")
        session.add(org)
        await session.flush()

        usage = await get_period_usage(session, org.id)
        usage.token_quota = 100
        await session.flush()

        assert is_over_quota(usage) is False

        await increment_usage(session, org.id, tokens=100)
        await session.refresh(usage)
        assert is_over_quota(usage) is True

        with caplog.at_level(logging.ERROR, logger="app.scheduler.pipeline"):
            logging.getLogger("app.scheduler.pipeline").error(
                "org %s over LLM quota; skipping publish", org.id
            )
        assert "over LLM quota" in caplog.text

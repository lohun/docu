from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.org_llm_usage import OrgLlmUsage


def current_period(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return (period_start, period_end) for the calendar month containing now."""
    now = now or datetime.now(timezone.utc)
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if period_start.month == 12:
        period_end = period_start.replace(year=period_start.year + 1, month=1)
    else:
        period_end = period_start.replace(month=period_start.month + 1)
    return period_start, period_end


async def get_period_usage(
    session: AsyncSession,
    org_id: int,
    now: datetime | None = None,
) -> OrgLlmUsage:
    period_start, period_end = current_period(now)
    usage = await session.scalar(
        select(OrgLlmUsage).where(
            OrgLlmUsage.org_id == org_id,
            OrgLlmUsage.period_start == period_start,
        )
    )
    if usage is None:
        usage = OrgLlmUsage(
            org_id=org_id,
            period_start=period_start,
            period_end=period_end,
            tokens_used=0,
            call_count=0,
        )
        session.add(usage)
        await session.flush()
    return usage


async def increment_usage(
    session: AsyncSession,
    org_id: int,
    tokens: int,
    now: datetime | None = None,
) -> OrgLlmUsage:
    usage = await get_period_usage(session, org_id, now)
    usage.tokens_used = usage.tokens_used + tokens
    usage.call_count = usage.call_count + 1
    await session.flush()
    return usage


def is_over_quota(usage: OrgLlmUsage) -> bool:
    return usage.token_quota > 0 and usage.tokens_used >= usage.token_quota

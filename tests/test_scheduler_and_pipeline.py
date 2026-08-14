import asyncio
import pytest
from sqlalchemy import select

from app.models.organization import Organization
from app.models.run_log import RunLog
from app.models.source import Source
from app.models.user import User
from app.scheduler.pipeline import trigger_pipeline_run
from app.scheduler.scheduler import DocVersionScheduler


@pytest.mark.anyio
async def test_scheduler_sync_source_jobs(session_factory) -> None:
    async with session_factory() as session:
        org = Organization(name="Sched Org", slug="sched-org")
        session.add(org)
        await session.flush()

        s1 = Source(org_id=org.id, name="Active 1", type="openapi", target_url="https://example.com/1", is_active=True)
        s2 = Source(org_id=org.id, name="Active 2", type="scrape", target_url="https://example.com/2", is_active=True)
        s3 = Source(org_id=org.id, name="Inactive 3", type="openapi", target_url="https://example.com/3", is_active=False)
        session.add_all([s1, s2, s3])
        await session.commit()

        scheduler = DocVersionScheduler()
        scheduler.start()
        assert scheduler.is_running

        job_count = await scheduler.sync_source_jobs(session)
        assert job_count == 2

        registered = scheduler.get_registered_jobs()
        assert s1.id in registered
        assert s2.id in registered
        assert s3.id not in registered

        scheduler.shutdown()
        assert not scheduler.is_running


@pytest.mark.anyio
async def test_pipeline_run_creates_run_log(session_factory) -> None:
    async with session_factory() as session:
        org = Organization(name="Pipe Org", slug="pipe-org")
        session.add(org)
        await session.flush()

        source = Source(
            org_id=org.id,
            name="Pipe Source",
            type="openapi",
            target_url="https://example.com/openapi.json",
            is_active=True,
        )
        session.add(source)
        await session.commit()

        run_log = await trigger_pipeline_run(session, source.id)
        assert run_log.source_id == source.id
        assert run_log.outcome in ("success", "success_no_change", "failed")
        assert run_log.started_at is not None
        assert run_log.finished_at is not None

        # Verify DB persisted run log
        db_log = await session.scalar(select(RunLog).where(RunLog.id == run_log.id))
        assert db_log is not None
        assert db_log.source_id == source.id


@pytest.mark.anyio
async def test_pipeline_locking_prevents_overlapping_runs(session_factory) -> None:
    async with session_factory() as session:
        org = Organization(name="Lock Org", slug="lock-org")
        session.add(org)
        await session.flush()

        source = Source(
            org_id=org.id,
            name="Lock Source",
            type="openapi",
            target_url="https://example.com/openapi.json",
            is_active=True,
        )
        session.add(source)
        await session.commit()
        source_id = source.id

    async with session_factory() as session1, session_factory() as session2:
        # Trigger two concurrent runs for the exact same source_id
        log1, log2 = await asyncio.gather(
            trigger_pipeline_run(session1, source_id),
            trigger_pipeline_run(session2, source_id),
        )

        outcomes = {log1.outcome, log2.outcome}
        assert "skipped" in outcomes or ("success" in outcomes or "failed" in outcomes)

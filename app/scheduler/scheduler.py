from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.jobs.retention import cleanup_expired_data
from app.models.source import Source
from app.scheduler.pipeline import trigger_pipeline_run
from app.db_urls import async_to_sync, with_sslmode


def _sync_database_url(async_url: str) -> str:
    return async_to_sync(async_url)


def _effective_database_url(database_url: str | None) -> str | None:
    if database_url is None:
        return None
    settings = get_settings()
    return with_sslmode(database_url, settings.database_sslmode)


class DocVersionScheduler:
    def __init__(
        self,
        database_url: str | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.database_url = _effective_database_url(database_url)
        self.session_factory = session_factory
        self._scheduler: AsyncIOScheduler | None = None
        self._jobs: dict[int, dict[str, Any]] = {}
        self._running = False

    def start(self) -> None:
        if self._scheduler is None and self.database_url:
            jobstores = {
                "default": SQLAlchemyJobStore(url=_sync_database_url(self.database_url)),
            }
            self._scheduler = AsyncIOScheduler(jobstores=jobstores)
            self._scheduler.add_job(
                self._run_retention,
                "cron",
                hour=3,
                minute=0,
                id="retention",
                replace_existing=True,
            )
            self._scheduler.start()
        self._running = True

    def shutdown(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        self._running = False
        self._jobs.clear()

    @property
    def is_running(self) -> bool:
        return self._running

    async def _run_source_pipeline(self, source_id: int) -> None:
        if self.session_factory is None:
            return
        async with self.session_factory() as session:
            await trigger_pipeline_run(session, source_id)

    async def _run_retention(self) -> None:
        if self.session_factory is None:
            return
        async with self.session_factory() as session:
            await cleanup_expired_data(session)

    async def sync_source_jobs(self, session: AsyncSession) -> int:
        result = await session.scalars(select(Source).where(Source.is_active.is_(True)))
        active_sources = list(result.all())

        current_source_ids = {s.id for s in active_sources}

        to_remove = [sid for sid in self._jobs if sid not in current_source_ids]
        for sid in to_remove:
            if self._scheduler is not None:
                job_id = f"source-{sid}"
                if self._scheduler.get_job(job_id):
                    self._scheduler.remove_job(job_id)
            del self._jobs[sid]

        for source in active_sources:
            self._jobs[source.id] = {
                "source_id": source.id,
                "interval_seconds": source.fetch_interval_seconds,
            }
            if self._scheduler is not None:
                job_id = f"source-{source.id}"
                self._scheduler.add_job(
                    self._run_source_pipeline,
                    "interval",
                    seconds=source.fetch_interval_seconds,
                    id=job_id,
                    args=[source.id],
                    replace_existing=True,
                )

        return len(self._jobs)

    def get_registered_jobs(self) -> dict[int, dict[str, Any]]:
        return self._jobs.copy()

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import compute_content_hash, get_adapter
from app.diff import get_diff_engine
from app.llm.client import LLMClient
from app.llm.metering import get_period_usage, increment_usage, is_over_quota
from app.llm.queue import LLMQueue
from app.models.diff import Diff
from app.models.doc_update import DocUpdate
from app.models.organization import Organization
from app.models.run_log import RunLog
from app.models.snapshot import Snapshot
from app.models.source import Source
from app.publish.db_publish import publish_initial_doc, publish_to_db, resolve_doc
from app.publish.git_export import GitExportError, export_doc_to_git
from app.storage import SnapshotStore, get_snapshot_store

logger = logging.getLogger(__name__)

_SOURCE_LOCKS: dict[int, asyncio.Lock] = {}
_llm_queue = LLMQueue()


def get_source_lock(source_id: int) -> asyncio.Lock:
    if source_id not in _SOURCE_LOCKS:
        _SOURCE_LOCKS[source_id] = asyncio.Lock()
    return _SOURCE_LOCKS[source_id]


def get_llm_queue() -> LLMQueue:
    return _llm_queue


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _get_latest_snapshot(session: AsyncSession, source_id: int) -> Snapshot | None:
    return await session.scalar(
        select(Snapshot)
        .where(Snapshot.source_id == source_id)
        .order_by(Snapshot.fetched_at.desc())
        .limit(1)
    )


async def _persist_snapshot(
    session: AsyncSession,
    source_id: int,
    content_hash: str,
    excerpt: str,
    raw_bytes: bytes,
    store: SnapshotStore,
    screenshot: bytes | None = None,
) -> Snapshot:
    snapshot = Snapshot(
        source_id=source_id,
        content_hash=content_hash,
        normalized_excerpt=excerpt,
        raw_storage_ref="",
    )
    session.add(snapshot)
    await session.flush()
    snapshot.raw_storage_ref = store.write_raw(snapshot.id, raw_bytes)
    if screenshot:
        snapshot.screenshot_storage_ref = store.write_raw(
            snapshot.id, screenshot, suffix="png"
        )
    return snapshot


async def _compute_diff(
    session: AsyncSession,
    source: Source,
    store: SnapshotStore,
    latest: Snapshot,
    new_snapshot: Snapshot,
) -> Diff:
    engine = get_diff_engine(source.type)
    diff_result = await engine.compute(store, latest, new_snapshot)
    diff = Diff(
        source_id=source.id,
        from_snapshot_id=latest.id,
        to_snapshot_id=new_snapshot.id,
        diff_type=diff_result.diff_type,
        diff_payload=diff_result.payload,
        is_trivial=diff_result.is_trivial,
    )
    session.add(diff)
    await session.flush()
    return diff

async def _generate_and_publish(
    session: AsyncSession,
    source: Source,
    diff: Diff,
) -> str:
    doc = await resolve_doc(session, source)

    usage = await get_period_usage(session, source.org_id)
    if is_over_quota(usage):
        logger.error("org %s over LLM quota; skipping publish", source.org_id)
        rejected = DocUpdate(
            source_id=source.id,
            diff_id=diff.id,
            doc_id=doc.id,
            section_key="",
            previous_content=doc.current_content_md,
            new_content="",
            status="rejected",
        )
        session.add(rejected)
        await session.flush()
        return "success_quota_rejected"

    client = LLMClient()

    if not client.is_available():
        logger.warning(
            "NVIDIA API key not configured; skipping LLM publish for source %s",
            source.id,
        )
        return "success_skipped"

    queue = get_llm_queue()
    section_update, token_usage = await queue.generate_section_update(
        client,
        doc.current_content_md,
        diff.diff_payload,
        None,
    )
    await increment_usage(session, source.org_id, token_usage)
    await publish_to_db(
        session,
        source,
        doc,
        diff,
        section_update,
        llm_model_used=client.model,
        token_usage=token_usage,
    )

    # DB publish is authoritative; git mirror failures must never roll it back.
    if doc.git_export_enabled:
        try:
            org = await session.get(Organization, source.org_id)
            export_doc_to_git(doc, org.slug if org else str(source.org_id))
        except (GitExportError, OSError) as e:
            logger.error(
                "git export failed for doc %s (DB publish unaffected): %s",
                doc.id,
                e,
            )
    return "success"


async def _generate_initial_doc(
    session: AsyncSession,
    source: Source,
    snapshot: Snapshot,
) -> str:
    """Generate initial documentation for a first-run source."""

    doc = await resolve_doc(session, source)

    usage = await get_period_usage(session, source.org_id)
    if is_over_quota(usage):
        logger.error("org %s over LLM quota; skipping initial doc generation", source.org_id)
        rejected = DocUpdate(
            source_id=source.id,
            diff_id=None,
            doc_id=doc.id,
            section_key="",
            previous_content=doc.current_content_md,
            new_content="",
            status="rejected",
        )
        session.add(rejected)
        await session.flush()
        return "success_quota_rejected"

    client = LLMClient()
    if not client.is_available():
        logger.warning(
            "NVIDIA API key not configured; skipping initial doc generation for source %s",
            source.id,
        )
        return "success_skipped"

    queue = get_llm_queue()
    initial_doc, token_usage = await queue.generate_initial_doc(
        client,
        snapshot.normalized_excerpt,
        source.type,
        source.name,
    )
    await increment_usage(session, source.org_id, token_usage)
    await publish_initial_doc(
        session,
        source,
        doc,
        initial_doc.full_content,
        llm_model_used=client.model,
        token_usage=token_usage,
    )

    # DB publish is authoritative; git mirror failures must never roll it back.
    if doc.git_export_enabled:
        try:
            org = await session.get(Organization, source.org_id)
            export_doc_to_git(doc, org.slug if org else str(source.org_id))
        except (GitExportError, OSError) as e:
            logger.error(
                "git export failed for doc %s (DB publish unaffected): %s",
                doc.id,
                e,
            )
    return "success_initial_doc"


async def _execute_pipeline(session: AsyncSession, source: Source, force_initial_doc: bool = False) -> str:
    adapter = get_adapter(source.type)
    result = await adapter.fetch(source)
    content_hash = compute_content_hash(result.normalized)

    latest = await _get_latest_snapshot(session, source.id)

    store = get_snapshot_store()
    new_snapshot = await _persist_snapshot(
        session,
        source.id,
        content_hash,
        result.excerpt,
        result.raw_bytes,
        store,
        result.screenshot,
    )

    # If forced initial doc or first run, generate initial documentation
    if force_initial_doc or latest is None:
        return await _generate_initial_doc(session, source, new_snapshot)

    if latest.content_hash == content_hash:
        return "success_no_change"

    diff = await _compute_diff(session, source, store, latest, new_snapshot)
    if diff.is_trivial:
        return "success_trivial"

    return await _generate_and_publish(session, source, diff)


async def trigger_pipeline_run(session: AsyncSession, source_id: int, force_initial_doc: bool = False) -> RunLog:
    source = await session.get(Source, source_id)
    if source is None:
        raise ValueError(f"Source with id {source_id} not found")

    lock = get_source_lock(source_id)

    if lock.locked():
        run_log = RunLog(
            source_id=source_id,
            started_at=_now(),
            finished_at=_now(),
            outcome="skipped",
            error_message="source pipeline run already in progress",
        )
        session.add(run_log)
        await session.commit()
        return run_log

    async with lock:
        run_log = RunLog(
            source_id=source_id,
            started_at=_now(),
            outcome="running",
        )
        session.add(run_log)
        await session.commit()
        await session.refresh(run_log)

        try:
            outcome = await _execute_pipeline(session, source, force_initial_doc=force_initial_doc)
            run_log.outcome = outcome
            run_log.finished_at = _now()
            await session.commit()
        except Exception as e:
            run_log.outcome = "failed"
            run_log.finished_at = _now()
            run_log.error_message = str(e)
            await session.commit()

        return run_log

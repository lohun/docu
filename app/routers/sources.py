from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_org, get_current_user, require_role
from app.db import get_session, get_source_with_doc
from app.models.diff import Diff
from app.models.doc import Doc
from app.models.doc_update import DocUpdate
from app.models.organization import Organization
from app.models.run_log import RunLog
from app.models.snapshot import Snapshot
from app.models.source import Source
from app.models.user import User
from app.publish.db_publish import slugify
from app.rate_limit import limiter
from app.schemas.sources import SourceCreate, SourceOut, SourceUpdate
from app.security import SSRFError, validate_target_url
from app.storage import get_snapshot_store

router = APIRouter(prefix="/orgs/{org_id}/sources", tags=["sources"])


def _run_now_rate_key(request: Request) -> str:
    """Rate limit run-now per org so one tenant can't exhaust shared capacity."""
    org_id = request.path_params.get("org_id", "anonymous")
    return f"run-now:{org_id}"


@router.get("", response_model=list[SourceOut])
async def list_sources(
    org_and_role: tuple[Organization, str] = Depends(get_current_org),
    session: AsyncSession = Depends(get_session),
) -> list[SourceOut]:
    org, role = org_and_role
    require_role("viewer", role)

    result = await session.scalars(
        select(Source).where(Source.org_id == org.id).order_by(Source.id.desc())
    )
    return list(result.all())


@router.post("", status_code=201, response_model=SourceOut)
async def create_source(
    payload: SourceCreate,
    org_and_role: tuple[Organization, str] = Depends(get_current_org),
    session: AsyncSession = Depends(get_session),
) -> SourceOut:
    org, role = org_and_role
    require_role("member", role)

    try:
        validated_url = validate_target_url(payload.target_url)
    except SSRFError as e:
        raise HTTPException(status_code=400, detail=f"invalid or restricted target URL: {e}") from e

    source = Source(
        org_id=org.id,
        name=payload.name,
        type=payload.type,
        target_url=validated_url,
        fetch_interval_seconds=payload.fetch_interval_seconds,
        css_scope_selector=payload.css_scope_selector,
        is_active=True,
    )
    session.add(source)
    await session.flush()
    await session.commit()
    await session.refresh(source)
    return source


@router.get("/{source_id}", response_model=SourceOut)
async def get_source(
    source_id: int,
    org_and_role: tuple[Organization, str] = Depends(get_current_org),
    session: AsyncSession = Depends(get_session),
) -> SourceOut:
    org, role = org_and_role
    require_role("viewer", role)

    source = await session.scalar(
        select(Source).where(Source.id == source_id, Source.org_id == org.id)
    )
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    return source


@router.patch("/{source_id}", response_model=SourceOut)
async def update_source(
    source_id: int,
    payload: SourceUpdate,
    org_and_role: tuple[Organization, str] = Depends(get_current_org),
    session: AsyncSession = Depends(get_session),
) -> SourceOut:
    org, role = org_and_role
    require_role("member", role)

    source = await session.scalar(
        select(Source).where(Source.id == source_id, Source.org_id == org.id)
    )
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")

    update_data = payload.model_dump(exclude_unset=True)
    if "target_url" in update_data and update_data["target_url"]:
        try:
            update_data["target_url"] = validate_target_url(update_data["target_url"])
        except SSRFError as e:
            raise HTTPException(status_code=400, detail=f"invalid or restricted target URL: {e}") from e

    for field, value in update_data.items():
        setattr(source, field, value)

    await session.commit()
    await session.refresh(source)
    return source


@router.delete("/{source_id}")
async def delete_source(
    source_id: int,
    org_and_role: tuple[Organization, str] = Depends(get_current_org),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    org, role = org_and_role
    require_role("admin", role)

    source = await session.scalar(
        select(Source).where(Source.id == source_id, Source.org_id == org.id)
    )
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")

    # Purge the source's snapshot blobs (raw + screenshot) from the configured
    # store before deleting the rows that reference them. Best-effort: a missing
    # or already-purged asset must never block the source deletion.
    snapshot_refs = (
        await session.execute(
            select(Snapshot.raw_storage_ref, Snapshot.screenshot_storage_ref).where(
                Snapshot.source_id == source.id
            )
        )
    ).all()
    store = get_snapshot_store()
    for raw_ref, screenshot_ref in snapshot_refs:
        for ref in (raw_ref, screenshot_ref):
            if not ref:
                continue
            try:
                store.delete_raw(ref)
            except Exception:
                continue

    # Delete all dependent rows explicitly so a deleted source leaves nothing
    # behind: docs (and their doc_updates audit trail), diffs, snapshots and run
    # logs. Order avoids violating the diff/snapshot/doc FK references.
    await session.execute(delete(DocUpdate).where(DocUpdate.source_id == source.id))
    await session.execute(delete(Diff).where(Diff.source_id == source.id))
    await session.execute(delete(Snapshot).where(Snapshot.source_id == source.id))
    await session.execute(delete(RunLog).where(RunLog.source_id == source.id))
    await session.execute(delete(Doc).where(Doc.source_id == source.id))

    await session.delete(source)
    await session.commit()
    return {"status": "deleted"}


@router.post("/{source_id}/run-now", status_code=202)
@limiter.limit("10/minute", key_func=_run_now_rate_key)
async def run_source_now(
    request: Request,
    source_id: int,
    org_and_role: tuple[Organization, str] = Depends(get_current_org),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    org, role = org_and_role
    require_role("member", role)

    source, doc = await get_source_with_doc(session, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    if source.org_id != org.id:
        raise HTTPException(status_code=404, detail="source not found")

    if not source.is_active:
        raise HTTPException(status_code=400, detail="cannot trigger inactive source")

    # Enqueue pipeline run (will be integrated with scheduler/pipeline service)
    try:
        from app.scheduler.pipeline import trigger_pipeline_run
        force_initial_doc = (doc is None or doc.current_content_md == "")
        await trigger_pipeline_run(session, source.id, force_initial_doc=force_initial_doc)
    except ImportError:
        pass  # Pipeline placeholder until scheduler module lands

    return {"status": "enqueued", "source_id": str(source.id)}


@router.get("/{source_id}/runs", response_model=list[dict])
async def list_source_runs(
    source_id: int,
    org_and_role: tuple[Organization, str] = Depends(get_current_org),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    org, role = org_and_role
    require_role("viewer", role)

    source = await session.scalar(
        select(Source).where(Source.id == source_id, Source.org_id == org.id)
    )
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")

    result = await session.scalars(
        select(RunLog)
        .where(RunLog.source_id == source.id)
        .order_by(RunLog.started_at.desc())
    )
    return [
        {
            "id": run.id,
            "source_id": run.source_id,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "outcome": run.outcome,
            "error_message": run.error_message,
        }
        for run in result.all()
    ]

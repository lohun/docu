from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import get_current_org, require_role
from app.auth.service import resolve_org_by_domain
from app.db import get_session
from app.models.diff import Diff
from app.models.doc import Doc
from app.models.doc_update import DocUpdate
from app.models.organization import Organization
from app.rate_limit import limiter
from app.schemas.docs import DiffViewOut, DocOut, DocUpdateOut

router = APIRouter(tags=["docs"])


async def _get_scoped_doc(
    session: AsyncSession,
    org_id: int,
    doc_id: int,
) -> Doc:
    doc = await session.scalar(
        select(Doc).where(Doc.id == doc_id, Doc.org_id == org_id)
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="doc not found")
    return doc


async def _get_scoped_diff(
    session: AsyncSession,
    org_id: int,
    doc_id: int,
    diff_id: int,
) -> Diff:
    diff = await session.scalar(
        select(Diff)
        .join(Doc, Doc.source_id == Diff.source_id)
        .where(
            Diff.id == diff_id,
            Doc.id == doc_id,
            Doc.org_id == org_id,
        )
    )
    if diff is None:
        raise HTTPException(status_code=404, detail="diff not found")
    return diff


@router.get("/orgs/{org_id}/docs", response_model=list[DocOut])
async def list_docs(
    org_and_role: tuple[Organization, str] = Depends(get_current_org),
    session: AsyncSession = Depends(get_session),
) -> list[DocOut]:
    org, role = org_and_role
    require_role("viewer", role)
    result = await session.scalars(
        select(Doc).where(Doc.org_id == org.id).order_by(Doc.updated_at.desc())
    )
    return list(result.all())


@router.get("/orgs/{org_id}/docs/{doc_id}", response_model=DocOut)
async def get_doc(
    doc_id: int,
    org_and_role: tuple[Organization, str] = Depends(get_current_org),
    session: AsyncSession = Depends(get_session),
) -> DocOut:
    org, role = org_and_role
    require_role("viewer", role)
    return await _get_scoped_doc(session, org.id, doc_id)


@router.get("/orgs/{org_id}/docs/{doc_id}/history", response_model=list[DocUpdateOut])
async def get_doc_history(
    doc_id: int,
    org_and_role: tuple[Organization, str] = Depends(get_current_org),
    session: AsyncSession = Depends(get_session),
) -> list[DocUpdateOut]:
    org, role = org_and_role
    require_role("viewer", role)
    await _get_scoped_doc(session, org.id, doc_id)

    result = await session.scalars(
        select(DocUpdate)
        .where(DocUpdate.doc_id == doc_id)
        .order_by(DocUpdate.created_at.desc())
    )
    return list(result.all())


@router.get(
    "/orgs/{org_id}/docs/{doc_id}/diffs/{diff_id}",
    response_model=DiffViewOut,
)
async def get_doc_diff_view(
    doc_id: int,
    diff_id: int,
    org_and_role: tuple[Organization, str] = Depends(get_current_org),
    session: AsyncSession = Depends(get_session),
) -> DiffViewOut:
    org, role = org_and_role
    require_role("viewer", role)
    diff = await _get_scoped_diff(session, org.id, doc_id, diff_id)

    update = await session.scalar(
        select(DocUpdate).where(DocUpdate.diff_id == diff.id)
    )
    return DiffViewOut(
        **{
            "id": diff.id,
            "source_id": diff.source_id,
            "from_snapshot_id": diff.from_snapshot_id,
            "to_snapshot_id": diff.to_snapshot_id,
            "diff_type": diff.diff_type,
            "diff_payload": diff.diff_payload,
            "is_trivial": diff.is_trivial,
            "created_at": diff.created_at,
        },
        resulting_update=(
            DocUpdateOut(
                id=update.id,
                source_id=update.source_id,
                diff_id=update.diff_id,
                doc_id=update.doc_id,
                section_key=update.section_key,
                previous_content=update.previous_content,
                new_content=update.new_content,
                llm_model_used=update.llm_model_used,
                token_usage=update.token_usage,
                status=update.status,
                created_at=update.created_at,
            )
            if update is not None
            else None
        ),
    )


async def _resolve_public_org(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Organization:
    host = request.headers.get("host", "")
    org = await resolve_org_by_domain(session, host)
    if org is None:
        raise HTTPException(status_code=404, detail="docs domain not recognized")
    return org


@router.get("/docs/{doc_slug}", response_model=DocOut)
@limiter.limit("100/minute")
async def get_public_doc(
    request: Request,
    doc_slug: str,
    org: Organization = Depends(_resolve_public_org),
    session: AsyncSession = Depends(get_session),
) -> DocOut:
    """Public doc read resolved by verified custom domain (Host header)."""
    doc = await session.scalar(
        select(Doc).where(Doc.org_id == org.id, Doc.slug == doc_slug)
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="doc not found")
    return doc

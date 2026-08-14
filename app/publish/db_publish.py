import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.doc import Doc
from app.models.doc_update import DocUpdate
from app.models.source import Source
from app.publish.section_apply import apply_section_update, extract_section


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "doc"


async def resolve_doc(session: AsyncSession, source: Source) -> Doc:
    """Return the doc target for a source. Doc is auto-created when source is created."""
    doc = await session.scalar(
        select(Doc).where(Doc.source_id == source.id)
    )
    if doc is not None:
        return doc

    # Fallback: create doc if it doesn't exist (shouldn't happen with new flow)
    doc = Doc(
        org_id=source.org_id,
        source_id=source.id,
        title=source.name,
        slug=slugify(source.name),
        current_content_md="",
        version=1,
    )
    session.add(doc)
    await session.flush()
    return doc


async def publish_to_db(
    session: AsyncSession,
    source: Source,
    doc: Doc,
    diff,
    section_update,
    llm_model_used: str | None = None,
    token_usage: int | None = None,
) -> DocUpdate:
    """Materialize a section update into docs.current_content_md and append an audit row.

    Idempotent per diff: re-publishing the same diff is a no-op that returns the
    existing DocUpdate rather than appending a duplicate.
    """
    existing = await session.scalar(
        select(DocUpdate).where(
            DocUpdate.diff_id == diff.id,
            DocUpdate.status == "published",
        )
    )
    if existing is not None:
        return existing

    previous = doc.current_content_md
    new_md = apply_section_update(previous, section_update.section_key, section_update.new_content)

    doc.current_content_md = new_md
    doc.version += 1
    doc.updated_at = datetime.now(timezone.utc)

    update = DocUpdate(
        source_id=source.id,
        diff_id=diff.id,
        doc_id=doc.id,
        section_key=section_update.section_key,
        previous_content=extract_section(previous, section_update.section_key),
        new_content=section_update.new_content,
        llm_model_used=llm_model_used,
        token_usage=token_usage,
        status="published",
    )
    session.add(update)
    await session.flush()
    return update


async def publish_initial_doc(
    session: AsyncSession,
    source: Source,
    doc: Doc,
    initial_content: str,
    llm_model_used: str | None = None,
    token_usage: int | None = None,
) -> DocUpdate:
    """Publish initial documentation for a first-run source.

    Sets the full doc content and creates an audit row with status 'initial'.
    """
    doc.current_content_md = initial_content
    doc.version += 1
    doc.updated_at = datetime.now(timezone.utc)

    update = DocUpdate(
        source_id=source.id,
        diff_id=None,
        doc_id=doc.id,
        section_key="",
        previous_content="",
        new_content=initial_content,
        llm_model_used=llm_model_used,
        token_usage=token_usage,
        status="initial",
    )
    session.add(update)
    await session.flush()
    return update

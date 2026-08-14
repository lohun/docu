from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import joinedload

from app.config import get_settings
from app.models.doc import Doc
from app.models.source import Source


def create_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(settings.database_url, echo=settings.debug)


engine = create_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def database_is_ready() -> bool:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return True


async def get_source_with_doc(session: AsyncSession, source_id: int) -> tuple[Source, Doc | None]:
    """Get a source with its associated doc using a left join.

    Returns a tuple of (source, doc) where doc may be None if no doc exists.
    """
    result = await session.execute(
        select(Source, Doc)
        .outerjoin(Doc, Doc.source_id == Source.id)
        .where(Source.id == source_id)
        .options(joinedload(Source.organization))
    )
    results = result.unique().all()
    if not results:
        return None, None
    source = results[0][0]
    doc = results[len(results) - 1][1]

    return source, doc

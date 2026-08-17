from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import backref, relationship

from app.models.base import Base, PKMixin


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Snapshot(Base, PKMixin):
    __tablename__ = "snapshots"

    source_id = Column(Integer, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    content_hash = Column(String(64), nullable=False)
    normalized_excerpt = Column(Text, nullable=True)
    fetched_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), default=_now)
    raw_storage_ref = Column(String(512), nullable=False)
    screenshot_storage_ref = Column(String(512), nullable=True)

    source = relationship("Source", backref=backref("snapshots", passive_deletes=True))

    __table_args__ = (
        Index("ix_snapshots_source_id_fetched_at", "source_id", "fetched_at"),
    )

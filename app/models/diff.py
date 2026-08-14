from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import Base, PKMixin


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Diff(Base, PKMixin):
    __tablename__ = "diffs"

    source_id = Column(Integer, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    from_snapshot_id = Column(Integer, ForeignKey("snapshots.id", ondelete="SET NULL"), nullable=True)
    to_snapshot_id = Column(Integer, ForeignKey("snapshots.id", ondelete="CASCADE"), nullable=False)
    diff_type = Column(String(20), nullable=False)  # "openapi" | "scrape"
    diff_payload = Column(JSONB, nullable=True)
    is_trivial = Column(Boolean, nullable=False, server_default="false", default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), default=_now)

    source = relationship("Source", backref="diffs")

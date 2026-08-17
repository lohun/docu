from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import backref, relationship

from app.models.base import Base, PKMixin


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RunLog(Base, PKMixin):
    __tablename__ = "run_logs"

    source_id = Column(Integer, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), default=_now)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    outcome = Column(String(30), nullable=False, default="running")  # "running" | "success" | "failed" | "skipped"
    error_message = Column(Text, nullable=True)

    source = relationship("Source", backref=backref("run_logs", passive_deletes=True))

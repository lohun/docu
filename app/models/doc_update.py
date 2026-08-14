from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.models.base import Base, PKMixin


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DocUpdate(Base, PKMixin):
    __tablename__ = "doc_updates"

    source_id = Column(Integer, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    diff_id = Column(Integer, ForeignKey("diffs.id", ondelete="SET NULL"), nullable=True)
    doc_id = Column(Integer, ForeignKey("docs.id", ondelete="CASCADE"), nullable=False)
    section_key = Column(String(255), nullable=False)
    previous_content = Column(Text, nullable=True)
    new_content = Column(Text, nullable=False)
    llm_model_used = Column(String(100), nullable=True)
    token_usage = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, server_default="published", default="published")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), default=_now)

    source = relationship("Source", backref="doc_updates")
    doc = relationship("Doc", backref="updates")

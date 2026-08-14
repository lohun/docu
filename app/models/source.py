from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.models.base import Base, PKMixin


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Source(Base, PKMixin):
    __tablename__ = "sources"

    org_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    type = Column(String(20), nullable=False)  # "openapi" | "scrape"
    target_url = Column(String(2048), nullable=False)
    fetch_interval_seconds = Column(Integer, nullable=False, server_default="300")
    css_scope_selector = Column(String(255), nullable=True)
    is_active = Column(Boolean, nullable=False, server_default="true", default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), default=_now)

    organization = relationship("Organization", backref="sources")

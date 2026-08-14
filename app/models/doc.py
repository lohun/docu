from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.models.base import Base, PKMixin


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Doc(Base, PKMixin):
    __tablename__ = "docs"

    org_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    source_id = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=False)
    current_content_md = Column(Text, nullable=False, server_default="")
    version = Column(Integer, nullable=False, server_default="1", default=1)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), default=_now)
    git_export_enabled = Column(Boolean, nullable=False, server_default="false", default=False)
    git_export_path = Column(String(512), nullable=True)
    last_git_export_commit = Column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_docs_org_id_slug"),
    )

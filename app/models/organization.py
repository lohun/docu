from datetime import datetime

from sqlalchemy import DateTime, Identity, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PKMixin


class Organization(PKMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    plan_tier: Mapped[str] = mapped_column(
        String(50),
        server_default="free",
        nullable=False,
    )
    custom_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    custom_domain_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

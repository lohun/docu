from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class OrgMembership(Base):
    __tablename__ = "org_memberships"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    invited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user: Mapped["User"] = relationship("User", back_populates="memberships")
    organization: Mapped["Organization"] = relationship("Organization")

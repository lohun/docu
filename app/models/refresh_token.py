from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Identity, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PKMixin


class RefreshToken(PKMixin, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

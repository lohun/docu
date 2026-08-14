from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer
from sqlalchemy.orm import relationship

from app.models.base import Base, PKMixin


class OrgLlmUsage(Base, PKMixin):
    __tablename__ = "org_llm_usage"

    org_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    period_start = Column(DateTime(timezone=True), nullable=False)
    period_end = Column(DateTime(timezone=True), nullable=False)
    tokens_used = Column(BigInteger, nullable=False, server_default="0", default=0)
    call_count = Column(Integer, nullable=False, server_default="0", default=0)
    token_quota = Column(BigInteger, nullable=False, server_default="0", default=0)

    organization = relationship("Organization", backref="llm_usage")

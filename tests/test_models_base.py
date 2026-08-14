import pytest
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, PKMixin, TimestampMixin


def test_naming_convention_configured() -> None:
    assert Base.metadata.naming_convention["ix"] == "ix_%(table_name)s_%(column_0_N_name)s"
    assert Base.metadata.naming_convention["uq"] == "uq_%(table_name)s_%(column_0_N_name)s"
    assert Base.metadata.naming_convention["fk"] == "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"


class ToyModel(PKMixin, TimestampMixin, Base):
    __tablename__ = "toy_models"
    name: Mapped[str] = mapped_column(String(255))


def test_mixins_produce_expected_columns() -> None:
    assert "id" in ToyModel.__table__.columns
    assert "created_at" in ToyModel.__table__.columns
    assert "updated_at" in ToyModel.__table__.columns
    assert isinstance(ToyModel.__table__.c.id.type, Integer)
    assert isinstance(ToyModel.__table__.c.created_at.type, DateTime)
    assert ToyModel.__table__.c.created_at.server_default is not None
    assert not ToyModel.__table__.c.updated_at.nullable

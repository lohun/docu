"""add email verification columns to users

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-01
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("email_verification_token_hash", sa.String(255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("email_verification_token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "email_verification_token_expires_at")
    op.drop_column("users", "email_verification_token_hash")
    op.drop_column("users", "email_verified_at")

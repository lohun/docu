"""add invite columns to users

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-01
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("invite_token_hash", sa.String(255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("invite_token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("invited_to_org_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_invited_to_org_id_organizations",
        "users",
        "organizations",
        ["invited_to_org_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_invited_to_org_id_organizations", "users", type_="foreignkey")
    op.drop_column("users", "invited_to_org_id")
    op.drop_column("users", "invite_token_expires_at")
    op.drop_column("users", "invite_token_hash")

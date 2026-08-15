"""auth session hardening: user token_version + refresh token families

Revision ID: 0006
Revises: fe69f7664515
Create Date: 2026-08-15

Adds `users.token_version` so a password change/disable can invalidate every
outstanding access JWT, and `refresh_tokens.family_id` so rotating a refresh
token can detect replay of an old (already rotated) token and revoke the whole
session lineage.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "fe69f7664515"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX_NAME = "ix_refresh_tokens_family_id"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.add_column(
        "refresh_tokens",
        sa.Column(
            "family_id",
            sa.String(length=64),
            # Backfill existing rows so the column can be NOT NULL.
            server_default=sa.text("md5((random()::text || clock_timestamp()::text))"),
            nullable=False,
        ),
    )
    op.create_index(_INDEX_NAME, "refresh_tokens", ["family_id"])
    # Drop the backfill default; new rows set family_id explicitly.
    op.alter_column("refresh_tokens", "family_id", server_default=None)


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "family_id")
    op.drop_column("users", "token_version")
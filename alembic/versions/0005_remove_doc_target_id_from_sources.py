"""remove doc_target_id from sources table to fix circular dependency

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "dda4699585d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the foreign key constraint first
    op.drop_constraint("fk_sources_doc_target_id_docs", "sources", type_="foreignkey")
    
    # Drop the doc_target_id column
    op.drop_column("sources", "doc_target_id")


def downgrade() -> None:
    # Re-add the doc_target_id column
    op.add_column("sources", sa.Column("doc_target_id", sa.Integer(), nullable=True))
    
    # Re-add the foreign key constraint
    op.create_foreign_key(
        "fk_sources_doc_target_id_docs",
        "sources",
        "docs",
        ["doc_target_id"],
        ["id"],
        ondelete="SET NULL",
    )

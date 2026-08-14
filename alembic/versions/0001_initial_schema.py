"""initial schema from design_spec.md section 6

Revision ID: 0001
Revises:
Create Date: 2026-08-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _now() -> sa.ColumnElement:
    return sa.func.now()


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("plan_tier", sa.String(50), nullable=False, server_default="free"),
        sa.Column("custom_domain", sa.String(255), nullable=True),
        sa.Column("custom_domain_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )

    op.create_table(
        "org_llm_usage",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tokens_used", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_quota", sa.BigInteger(), nullable=False, server_default="0"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "org_memberships",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "docs",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("current_content_md", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("git_export_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("git_export_path", sa.String(512), nullable=True),
        sa.Column("last_git_export_commit", sa.String(64), nullable=True),
        sa.UniqueConstraint("org_id", "slug", name="uq_docs_org_id_slug"),
    )

    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("target_url", sa.String(2048), nullable=False),
        sa.Column("fetch_interval_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("css_scope_selector", sa.String(255), nullable=True),
        sa.Column("doc_target_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
    )

    op.create_table(
        "snapshots",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("normalized_excerpt", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("raw_storage_ref", sa.String(512), nullable=False),
    )

    op.create_table(
        "diffs",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_snapshot_id", sa.Integer(), sa.ForeignKey("snapshots.id", ondelete="SET NULL"), nullable=True),
        sa.Column("to_snapshot_id", sa.Integer(), sa.ForeignKey("snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("diff_type", sa.String(20), nullable=False),
        sa.Column("diff_payload", JSONB(), nullable=True),
        sa.Column("is_trivial", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
    )

    op.create_table(
        "run_logs",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
    )

    op.create_table(
        "doc_updates",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("diff_id", sa.Integer(), sa.ForeignKey("diffs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("doc_id", sa.Integer(), sa.ForeignKey("docs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_key", sa.String(255), nullable=False),
        sa.Column("previous_content", sa.Text(), nullable=True),
        sa.Column("new_content", sa.Text(), nullable=False),
        sa.Column("llm_model_used", sa.String(100), nullable=True),
        sa.Column("token_usage", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="published"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
    )

    op.create_foreign_key(
        "fk_sources_doc_target_id_docs",
        "sources",
        "docs",
        ["doc_target_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_docs_source_id_sources",
        "docs",
        "sources",
        ["source_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.create_index(
        "ix_snapshots_source_id_fetched_at",
        "snapshots",
        ["source_id", sa.text("fetched_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_docs_org_id_updated_at",
        "docs",
        ["org_id", sa.text("updated_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_docs_org_id_updated_at", table_name="docs")
    op.drop_index("ix_snapshots_source_id_fetched_at", table_name="snapshots")
    op.drop_constraint("fk_docs_source_id_sources", "docs", type_="foreignkey")
    op.drop_constraint("fk_sources_doc_target_id_docs", "sources", type_="foreignkey")
    op.drop_table("doc_updates")
    op.drop_table("run_logs")
    op.drop_table("diffs")
    op.drop_table("snapshots")
    op.drop_table("sources")
    op.drop_table("docs")
    op.drop_table("refresh_tokens")
    op.drop_table("org_memberships")
    op.drop_table("users")
    op.drop_table("org_llm_usage")
    op.drop_table("organizations")

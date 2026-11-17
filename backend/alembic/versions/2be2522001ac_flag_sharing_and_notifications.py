"""flag sharing: shared flags, their discussion, and in-app notifications

Revision ID: 2be2522001ac
Revises: 9becdde4c286
Create Date: 2026-11-16 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '2be2522001ac'
down_revision: Union[str, None] = '9becdde4c286'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("record_flags", sa.Column("shared_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("record_flags", sa.Column("shared_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True))

    op.create_table(
        "flag_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("flag_id", sa.Integer(), sa.ForeignKey("record_flags.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_flag_comments_tenant_id", "flag_comments", ["tenant_id"])
    op.create_index("ix_flag_comments_flag_id", "flag_comments", ["flag_id"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("flag_id", sa.Integer(), sa.ForeignKey("record_flags.id", ondelete="CASCADE"), nullable=True),
        sa.Column("record_id", sa.Integer(), sa.ForeignKey("records.id", ondelete="CASCADE"), nullable=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_notifications_tenant_id", "notifications", ["tenant_id"])
    op.create_index("ix_notifications_user_read", "notifications", ["user_id", "read_at"])

    for value in ("flag_shared", "flag_unshared", "flag_comment_added"):
        op.execute(f"ALTER TYPE auditaction ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("flag_comments")
    op.drop_column("record_flags", "shared_by")
    op.drop_column("record_flags", "shared_at")
    # PostgreSQL does not support removing enum values; they stay.

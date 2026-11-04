"""record flags: indexers escalate an issue on a record to a supervisor

Revision ID: 8ecac36541a6
Revises: f4a5b6c7d8e9
Create Date: 2026-11-04 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '8ecac36541a6'
down_revision: Union[str, None] = 'f4a5b6c7d8e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "record_flags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("record_id", sa.Integer(), sa.ForeignKey("records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("batches.id"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("raised_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("raised_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("closed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
    )
    op.create_index("ix_record_flags_tenant_id", "record_flags", ["tenant_id"])
    op.create_index("ix_record_flags_record_id", "record_flags", ["record_id"])
    op.create_index("ix_record_flags_batch_id", "record_flags", ["batch_id"])
    op.create_index("ix_record_flags_project_status", "record_flags", ["project_id", "status"])
    # One open flag per record, enforced by the database as well as the service.
    op.create_index(
        "uq_record_flags_one_open", "record_flags", ["record_id"], unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    for value in ("record_flagged", "flag_withdrawn", "flag_resolved"):
        op.execute(f"ALTER TYPE auditaction ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    op.drop_table("record_flags")
    # PostgreSQL does not support removing enum values; they stay.

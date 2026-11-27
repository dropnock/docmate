"""customer QC supervisor review: awaiting_decision lots, mark overrides

Revision ID: a7c9e1f3b5d2
Revises: e7a3c5b1d9f2
Create Date: 2026-11-27 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a7c9e1f3b5d2'
down_revision: Union[str, None] = 'e7a3c5b1d9f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE lotstatus ADD VALUE IF NOT EXISTS 'awaiting_decision' AFTER 'qc_in_progress'")
    for value in ("qc_mark_overridden", "qc_lot_decided"):
        op.execute(f"ALTER TYPE auditaction ADD VALUE IF NOT EXISTS '{value}'")

    for table in ("lots", "lot_qc_rounds"):
        op.add_column(table, sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("decided_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True))

    op.add_column("qc_field_results", sa.Column("override_status", sa.String(10), nullable=True))
    op.add_column("qc_field_results", sa.Column("override_error_type", sa.String(20), nullable=True))
    op.add_column("qc_field_results", sa.Column("override_severity", sa.String(10), nullable=True))
    op.add_column("qc_field_results", sa.Column("override_note", sa.String(1024), nullable=True))
    op.add_column(
        "qc_field_results", sa.Column("overridden_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.add_column("qc_field_results", sa.Column("overridden_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for col in (
        "overridden_at", "overridden_by", "override_note", "override_severity", "override_error_type",
        "override_status",
    ):
        op.drop_column("qc_field_results", col)
    for table in ("lot_qc_rounds", "lots"):
        op.drop_column(table, "decided_by")
        op.drop_column(table, "decided_at")
    # PostgreSQL does not support removing enum values; they stay.

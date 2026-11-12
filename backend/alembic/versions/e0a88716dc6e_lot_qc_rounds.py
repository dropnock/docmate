"""lot QC rounds: lot_qc_rounds table, lots.qc_round, qc_field_results.qc_round,
auditaction 'resubmitted'

A lot the customer failed and sent for remediation can now be resubmitted.
Its finished round is archived in lot_qc_rounds; QC field marks carry the
round they belong to so a later round's tally and verdict don't count
earlier marks. Existing lots and marks are round 1.

Revision ID: e0a88716dc6e
Revises: 76966634aa8c
Create Date: 2026-11-12 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PgEnum

revision: str = 'e0a88716dc6e'
down_revision: Union[str, None] = '76966634aa8c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'resubmitted'")
    op.add_column("lots", sa.Column("qc_round", sa.Integer(), server_default="1", nullable=False))
    op.add_column("qc_field_results", sa.Column("qc_round", sa.Integer(), server_default="1", nullable=False))
    lotstatus = PgEnum(
        "draft", "released", "qc_in_progress", "passed", "failed", "remediation",
        name="lotstatus", create_type=False,
    )
    aqlstatus = PgEnum("normal", "tightened", "reduced", name="aqlstatus", create_type=False)
    op.create_table(
        "lot_qc_rounds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("status", lotstatus, nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("sample_rate", sa.Float(), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=True),
        sa.Column("acceptance_number", sa.Integer(), nullable=True),
        sa.Column("accuracy_rate", sa.Float(), nullable=True),
        sa.Column("aql_status_snapshot", aqlstatus, nullable=True),
        sa.Column("critical_defect_count", sa.Integer(), nullable=True),
        sa.Column("minor_defect_count", sa.Integer(), nullable=True),
        sa.Column("sampled_record_ids", sa.JSON(), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("qc_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resubmitted_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["lot_id"], ["lots.id"]),
        sa.ForeignKeyConstraint(["resubmitted_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lot_id", "round_number", name="uq_lot_qc_round"),
    )
    op.create_index("ix_lot_qc_rounds_tenant_id", "lot_qc_rounds", ["tenant_id"])
    op.create_index("ix_lot_qc_rounds_lot_id", "lot_qc_rounds", ["lot_id"])


def downgrade() -> None:
    op.drop_index("ix_lot_qc_rounds_lot_id", table_name="lot_qc_rounds")
    op.drop_index("ix_lot_qc_rounds_tenant_id", table_name="lot_qc_rounds")
    op.drop_table("lot_qc_rounds")
    op.drop_column("qc_field_results", "qc_round")
    op.drop_column("lots", "qc_round")
    # PostgreSQL does not support removing enum values; 'resubmitted' stays.

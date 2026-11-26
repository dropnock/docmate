"""Critical / major / minor non-conformity classes, each with its own AQL

Revision ID: b5e8c3a1d7f2
Revises: 9d4f2a7e1c3b
Create Date: 2026-11-26 00:00:00.000000

"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b5e8c3a1d7f2'
down_revision: Union[str, None] = '9d4f2a7e1c3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_ERROR_CLASSES = {"missing": "major", "typo": "minor", "interpretation": "minor"}


def upgrade() -> None:
    # The project's AQL (normal_aql) is the major AQL; minor starts at the
    # same value; critical starts as zero tolerance (NULL), which was the rule.
    op.add_column("aql_configs", sa.Column("critical_aql", sa.Float(), nullable=True))
    op.add_column("aql_configs", sa.Column("minor_aql", sa.Float(), nullable=True))
    op.add_column("aql_configs", sa.Column("error_classes", sa.JSON(), nullable=True))
    op.execute(
        sa.text("UPDATE aql_configs SET minor_aql = normal_aql, error_classes = CAST(:ec AS JSON)")
        .bindparams(ec=json.dumps(DEFAULT_ERROR_CLASSES))
    )
    op.alter_column("aql_configs", "minor_aql", nullable=False)
    op.alter_column("aql_configs", "error_classes", nullable=False)

    # Marks made so far: identifier-field (x-critical) errors are critical,
    # every other defect major; no error type was recorded.
    for table in ("qc_field_results", "lot_inspection_field_results"):
        op.add_column(table, sa.Column("error_type", sa.String(20), nullable=True))
        op.add_column(table, sa.Column("severity", sa.String(10), nullable=True))
        op.execute(
            f"UPDATE {table} SET severity = CASE WHEN is_critical THEN 'critical' ELSE 'major' END "
            "WHERE status = 'defective'"
        )

    # NULL class_plan = judged by the old rule, so verdicts already given
    # (and lots part-way through) don't change.
    for table in ("lot_inspections", "lots", "lot_qc_rounds"):
        op.add_column(table, sa.Column("class_plan", sa.JSON(), nullable=True))
        op.add_column(table, sa.Column("major_defect_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    for table in ("lot_qc_rounds", "lots", "lot_inspections"):
        op.drop_column(table, "major_defect_count")
        op.drop_column(table, "class_plan")
    for table in ("lot_inspection_field_results", "qc_field_results"):
        op.drop_column(table, "severity")
        op.drop_column(table, "error_type")
    for col in ("error_classes", "minor_aql", "critical_aql"):
        op.drop_column("aql_configs", col)

"""add lot qc summary fields (aql_status_snapshot, qc_completed_at, critical/minor_defect_count)

Revision ID: f4a5b6c7d8e9
Revises: e1f2a3b4c5d6
Create Date: 2026-08-13 06:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PgEnum

revision: str = 'f4a5b6c7d8e9'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # aqlstatus already exists (created for aql_configs.current_status in
    # 4lequxy65bsv_initial_schema.py) — reference it, don't recreate it.
    aqlstatus_enum = PgEnum("normal", "tightened", "reduced", name="aqlstatus", create_type=False)
    op.add_column("lots", sa.Column("aql_status_snapshot", aqlstatus_enum, nullable=True))
    op.add_column("lots", sa.Column("qc_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("lots", sa.Column("critical_defect_count", sa.Integer(), nullable=True))
    op.add_column("lots", sa.Column("minor_defect_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("lots", "minor_defect_count")
    op.drop_column("lots", "critical_defect_count")
    op.drop_column("lots", "qc_completed_at")
    op.drop_column("lots", "aql_status_snapshot")

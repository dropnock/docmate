"""Add index on records(status) and records(batch_id, status)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-03 00:00:00.000000

"""
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_records_status", "records", ["status"])
    op.create_index("ix_records_batch_id_status", "records", ["batch_id", "status"])
    op.create_index("ix_tasks_completed_at", "tasks", ["completed_at"])


def downgrade() -> None:
    op.drop_index("ix_tasks_completed_at", table_name="tasks")
    op.drop_index("ix_records_batch_id_status", table_name="records")
    op.drop_index("ix_records_status", table_name="records")

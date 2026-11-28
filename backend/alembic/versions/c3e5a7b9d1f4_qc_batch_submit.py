"""customer QC batches are submitted by the reviewer: close finished ones

QC batches now stay open (status 'indexing') until the reviewer submits
them, so reviewers can go back to records. Batches whose records were all
reviewed before this change are closed here, so they don't reappear in
reviewers' queues waiting to be submitted.

Revision ID: c3e5a7b9d1f4
Revises: a7c9e1f3b5d2
Create Date: 2026-11-27 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'c3e5a7b9d1f4'
down_revision: Union[str, None] = 'a7c9e1f3b5d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE batches SET status = 'complete'
        WHERE batch_type = 'qc' AND status = 'indexing'
          AND NOT EXISTS (
            SELECT 1 FROM tasks
            WHERE tasks.batch_id = batches.id AND tasks.status IN ('pending', 'in_progress')
          )
        """
    )


def downgrade() -> None:
    # Closed QC batches are left closed; nothing reads the difference once
    # every task is finished.
    pass

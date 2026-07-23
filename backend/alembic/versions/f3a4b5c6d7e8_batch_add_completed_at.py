"""batch add completed_at

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-07-22 00:00:02.000000

"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, None] = 'e2f3a4b5c6d7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("batches", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("batches", "completed_at")

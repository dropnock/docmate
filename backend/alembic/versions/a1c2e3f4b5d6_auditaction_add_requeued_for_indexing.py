"""auditaction add requeued_for_indexing value

Revision ID: a1c2e3f4b5d6
Revises: f3a4b5c6d7e8
Create Date: 2026-08-03 00:00:01.000000

"""
from typing import Union

from alembic import op

revision: str = 'a1c2e3f4b5d6'
down_revision: Union[str, None] = 'f3a4b5c6d7e8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'requeued_for_indexing'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op
    pass

"""recordstatus add disqualified value

Revision ID: d6e7f8a9b0c1
Revises: c9d1e2f3a4b5
Create Date: 2026-07-15 00:00:00.000000

"""
from typing import Union

from alembic import op

revision: str = 'd6e7f8a9b0c1'
down_revision: Union[str, None] = 'c9d1e2f3a4b5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE recordstatus ADD VALUE IF NOT EXISTS 'disqualified'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op
    pass

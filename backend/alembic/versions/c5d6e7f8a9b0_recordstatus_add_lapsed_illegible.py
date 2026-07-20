"""recordstatus add lapsed/illegible values

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-07-20 00:00:00.000000

"""
from typing import Union

from alembic import op

revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, None] = 'b4c5d6e7f8a9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE recordstatus ADD VALUE IF NOT EXISTS 'lapsed'")
    op.execute("ALTER TYPE recordstatus ADD VALUE IF NOT EXISTS 'illegible'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op
    pass

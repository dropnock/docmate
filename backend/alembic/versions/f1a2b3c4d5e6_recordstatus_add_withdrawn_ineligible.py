"""recordstatus add withdrawn/ineligible values

Revision ID: f1a2b3c4d5e6
Revises: e7f8a9b0c1d2
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Union

from alembic import op

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'e7f8a9b0c1d2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE recordstatus ADD VALUE IF NOT EXISTS 'withdrawn'")
    op.execute("ALTER TYPE recordstatus ADD VALUE IF NOT EXISTS 'ineligible'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op
    pass

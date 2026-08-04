"""auditaction add requeued_for_qa value

Revision ID: b2d3f4a5c6e7
Revises: a1c2e3f4b5d6
Create Date: 2026-08-03 00:00:02.000000

"""
from typing import Union

from alembic import op

revision: str = 'b2d3f4a5c6e7'
down_revision: Union[str, None] = 'a1c2e3f4b5d6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'requeued_for_qa'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op
    pass

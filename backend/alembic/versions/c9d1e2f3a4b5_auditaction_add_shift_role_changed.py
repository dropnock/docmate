"""auditaction add shift_role_changed value

Revision ID: c9d1e2f3a4b5
Revises: b8c9d1e2f3a4
Create Date: 2026-07-05 00:00:04.000000

"""
from typing import Union

from alembic import op

revision: str = 'c9d1e2f3a4b5'
down_revision: Union[str, None] = 'b8c9d1e2f3a4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'shift_role_changed'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op
    pass

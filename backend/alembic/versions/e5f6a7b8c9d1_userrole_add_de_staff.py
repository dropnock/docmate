"""userrole add de_staff value

Revision ID: e5f6a7b8c9d1
Revises: d4e5f6a7b8c9
Create Date: 2026-07-05 00:00:00.000000

"""
from typing import Union

from alembic import op

revision: str = 'e5f6a7b8c9d1'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'de_staff'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op
    pass

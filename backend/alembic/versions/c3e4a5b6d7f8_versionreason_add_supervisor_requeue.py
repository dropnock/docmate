"""versionreason add supervisor_requeue value

Revision ID: c3e4a5b6d7f8
Revises: b2d3f4a5c6e7
Create Date: 2026-08-03 00:00:03.000000

"""
from typing import Union

from alembic import op

revision: str = 'c3e4a5b6d7f8'
down_revision: Union[str, None] = 'b2d3f4a5c6e7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE versionreason ADD VALUE IF NOT EXISTS 'supervisor_requeue'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op
    pass

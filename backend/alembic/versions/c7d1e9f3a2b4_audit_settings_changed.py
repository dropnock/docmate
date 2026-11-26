"""audit action settings_changed: a project's details or AQLs were edited

Revision ID: c7d1e9f3a2b4
Revises: b5e8c3a1d7f2
Create Date: 2026-11-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'c7d1e9f3a2b4'
down_revision: Union[str, None] = 'b5e8c3a1d7f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'settings_changed'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; it stays.
    pass

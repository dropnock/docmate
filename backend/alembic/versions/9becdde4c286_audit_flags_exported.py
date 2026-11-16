"""audit action flags_exported: a supervisor downloaded the flag list as CSV

Revision ID: 9becdde4c286
Revises: 31e5d2d62e07
Create Date: 2026-11-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = '9becdde4c286'
down_revision: Union[str, None] = '31e5d2d62e07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'flags_exported'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; it stays.
    pass

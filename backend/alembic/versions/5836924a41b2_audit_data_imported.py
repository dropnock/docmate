"""audit action data_imported: JSON data attached to a record its image created

Revision ID: 5836924a41b2
Revises: 8ecac36541a6
Create Date: 2026-11-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = '5836924a41b2'
down_revision: Union[str, None] = '8ecac36541a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'data_imported'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; it stays.
    pass

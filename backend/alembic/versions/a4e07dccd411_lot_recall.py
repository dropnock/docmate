"""lot recall: lotinspectionstatus 'superseded', auditaction 'recalled' / 'lot_records_removed'

Recalling a released lot (or removing records from a draft lot after it
passed inspection) marks the passing inspection 'superseded', so the lot
needs a fresh outgoing inspection before it can be released again.

Revision ID: a4e07dccd411
Revises: 214768b2c46b
Create Date: 2026-11-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'a4e07dccd411'
down_revision: Union[str, None] = '214768b2c46b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE lotinspectionstatus ADD VALUE IF NOT EXISTS 'superseded'")
    for value in ("recalled", "lot_records_removed"):
        op.execute(f"ALTER TYPE auditaction ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; they stay.
    pass

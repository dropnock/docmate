"""auditentitytype: add 'cabinet'

Cabinet creation used to be logged as entity_type='record' with the cabinet's
id, which put it in the history of whichever record shares that id. Lot events
had the same problem; 'lot' already exists (c6d7e8f9a0b1).

Existing misfiled rows are NOT rewritten here: audit_logs is append-only and
retyping rows is a deliberate, reviewable step. Run the dry-run-by-default
scripts/backfill_lot_audit_entity_type.py afterwards.

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-11-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'd7e8f9a0b1c2'
down_revision: Union[str, None] = 'c6d7e8f9a0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE auditentitytype ADD VALUE IF NOT EXISTS 'cabinet'")


def downgrade() -> None:
    # Postgres can't drop an enum value; leaving it is harmless.
    pass

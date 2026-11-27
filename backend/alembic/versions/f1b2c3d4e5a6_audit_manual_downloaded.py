"""audit: entity type 'organization', action 'manual_downloaded'

Customer users can download the manuals as PDFs stamped with their
organisation's name; each download is audited against the organisation.

Revision ID: f1b2c3d4e5a6
Revises: e7a3c5b1d9f2
Create Date: 2026-11-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'f1b2c3d4e5a6'
down_revision: Union[str, None] = 'e7a3c5b1d9f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE auditentitytype ADD VALUE IF NOT EXISTS 'organization'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'manual_downloaded'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; both stay.
    pass

"""backfill users.role de_indexer/de_qa_agent -> de_staff

Revision ID: b8c9d1e2f3a4
Revises: a7b8c9d1e2f3
Create Date: 2026-07-05 00:00:03.000000

"""
from typing import Union

from alembic import op

revision: str = 'b8c9d1e2f3a4'
down_revision: Union[str, None] = 'a7b8c9d1e2f3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE users
        SET role = 'de_staff'
        WHERE role IN ('de_indexer', 'de_qa_agent')
    """)


def downgrade() -> None:
    # Cannot reliably know which de_staff users were formerly indexer vs
    # qa_agent; this is a one-way data migration. No-op downgrade.
    pass

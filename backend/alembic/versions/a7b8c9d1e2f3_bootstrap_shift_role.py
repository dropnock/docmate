"""bootstrap shift_role from legacy de_indexer/de_qa_agent role

Revision ID: a7b8c9d1e2f3
Revises: f6a7b8c9d1e2
Create Date: 2026-07-05 00:00:02.000000

"""
from typing import Union

from alembic import op

revision: str = 'a7b8c9d1e2f3'
down_revision: Union[str, None] = 'f6a7b8c9d1e2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Seed shift_role from each user's current (pre-collapse) legacy role so
    # existing rosters aren't dumped into "Unassigned" the moment this ships —
    # only rows changed by a supervisor going forward should move.
    op.execute("""
        UPDATE user_project_assignments upa
        SET shift_role = 'indexer'
        FROM users u
        WHERE u.id = upa.user_id
          AND u.role = 'de_indexer'
          AND upa.is_active = true
    """)
    op.execute("""
        UPDATE user_project_assignments upa
        SET shift_role = 'qa'
        FROM users u
        WHERE u.id = upa.user_id
          AND u.role = 'de_qa_agent'
          AND upa.is_active = true
    """)


def downgrade() -> None:
    # One-way data migration; no-op downgrade.
    pass

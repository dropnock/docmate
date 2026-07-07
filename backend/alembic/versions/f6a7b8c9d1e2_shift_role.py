"""add shiftrole enum and user_project_assignments.shift_role

Revision ID: f6a7b8c9d1e2
Revises: e5f6a7b8c9d1
Create Date: 2026-07-05 00:00:01.000000

"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PgEnum

revision: str = 'f6a7b8c9d1e2'
down_revision: Union[str, None] = 'e5f6a7b8c9d1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    sa.Enum("indexer", "qa", name="shiftrole").create(op.get_bind(), checkfirst=True)
    shiftrole_enum = PgEnum("indexer", "qa", name="shiftrole", create_type=False)
    op.add_column(
        "user_project_assignments",
        sa.Column("shift_role", shiftrole_enum, nullable=True),
    )
    op.create_index(
        "ix_user_project_assignments_shift_role",
        "user_project_assignments",
        ["shift_role"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_project_assignments_shift_role", table_name="user_project_assignments")
    op.drop_column("user_project_assignments", "shift_role")
    sa.Enum(name="shiftrole").drop(op.get_bind(), checkfirst=True)

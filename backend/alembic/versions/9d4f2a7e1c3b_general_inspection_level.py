"""ISO 2859-1 general inspection level (I / II / III) per project

Revision ID: 9d4f2a7e1c3b
Revises: 7c1e9a4d2b6f
Create Date: 2026-11-19 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '9d4f2a7e1c3b'
down_revision: Union[str, None] = '7c1e9a4d2b6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Every plan so far used level II, so existing projects and inspections
    # are recorded as II.
    op.add_column(
        "aql_configs",
        sa.Column("general_level", sa.String(3), nullable=False, server_default="II"),
    )
    op.add_column(
        "lot_inspections",
        sa.Column("general_level", sa.String(3), nullable=False, server_default="II"),
    )


def downgrade() -> None:
    op.drop_column("lot_inspections", "general_level")
    op.drop_column("aql_configs", "general_level")

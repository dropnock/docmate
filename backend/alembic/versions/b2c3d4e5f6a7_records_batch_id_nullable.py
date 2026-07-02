"""records batch_id nullable

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-30 00:00:00.000000

"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('records', 'batch_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column('records', 'batch_id', existing_type=sa.Integer(), nullable=False)

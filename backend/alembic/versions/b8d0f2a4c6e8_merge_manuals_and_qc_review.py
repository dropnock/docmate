"""merge heads: in-app manuals audit values + QC supervisor review

Revision ID: b8d0f2a4c6e8
Revises: f1b2c3d4e5a6, a7c9e1f3b5d2
Create Date: 2026-11-27 00:00:00.000000

"""
from typing import Sequence, Union

revision: str = 'b8d0f2a4c6e8'
down_revision: Union[str, Sequence[str], None] = ('f1b2c3d4e5a6', 'a7c9e1f3b5d2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

"""merge heads: manuals + QC review (b8d0f2a4c6e8) and QC batch submit

Revision ID: d5f7b9c1e3a6
Revises: b8d0f2a4c6e8, c3e5a7b9d1f4
Create Date: 2026-11-27 12:30:00.000000

"""
from typing import Sequence, Union

revision: str = 'd5f7b9c1e3a6'
down_revision: Union[str, Sequence[str], None] = ('b8d0f2a4c6e8', 'c3e5a7b9d1f4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

"""merge heads: data_imported audit action and lot QC rounds

Revision ID: 31aaeed4bb83
Revises: 5836924a41b2, e0a88716dc6e
Create Date: 2026-11-13 00:00:00.000000

"""
from typing import Sequence, Union

revision: str = '31aaeed4bb83'
down_revision: Union[str, Sequence[str], None] = ('5836924a41b2', 'e0a88716dc6e')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

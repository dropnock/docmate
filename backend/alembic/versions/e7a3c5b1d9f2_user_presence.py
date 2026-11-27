"""users: last_seen_at, session_started_at — who's online (services/presence_service.py)

Revision ID: e7a3c5b1d9f2
Revises: d4f2a8c1e6b9
Create Date: 2026-11-27 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e7a3c5b1d9f2'
down_revision: Union[str, None] = 'd4f2a8c1e6b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("session_started_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_last_seen_at", "users", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_users_last_seen_at", table_name="users")
    op.drop_column("users", "session_started_at")
    op.drop_column("users", "last_seen_at")

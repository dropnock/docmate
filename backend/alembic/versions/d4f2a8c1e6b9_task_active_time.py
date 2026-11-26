"""tasks: active_seconds, sessions, last_active_at — time spent added up
across sessions (services/task_timing.py)

Revision ID: d4f2a8c1e6b9
Revises: c7d1e9f3a2b4
Create Date: 2026-11-26 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd4f2a8c1e6b9'
down_revision: Union[str, None] = 'c7d1e9f3a2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("active_seconds", sa.Integer(), server_default="0", nullable=False))
    op.add_column("tasks", sa.Column("sessions", sa.Integer(), server_default="0", nullable=False))
    op.add_column("tasks", sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "last_active_at")
    op.drop_column("tasks", "sessions")
    op.drop_column("tasks", "active_seconds")

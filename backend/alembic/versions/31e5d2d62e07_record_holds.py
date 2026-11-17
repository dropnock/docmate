"""record holds: a supervisor holds an unassigned record back from assignment

Revision ID: 31e5d2d62e07
Revises: 31aaeed4bb83
Create Date: 2026-11-16 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '31e5d2d62e07'
down_revision: Union[str, None] = '31aaeed4bb83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "record_holds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("record_id", sa.Integer(), sa.ForeignKey("records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("held_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("held_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("released_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_note", sa.Text(), nullable=True),
    )
    op.create_index("ix_record_holds_tenant_id", "record_holds", ["tenant_id"])
    op.create_index("ix_record_holds_record_id", "record_holds", ["record_id"])
    op.create_index("ix_record_holds_project_active", "record_holds", ["project_id", "released_at"])
    # One active hold per record, enforced by the database as well as the service.
    op.create_index(
        "uq_record_holds_one_active", "record_holds", ["record_id"], unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )
    for value in ("record_held", "hold_released"):
        op.execute(f"ALTER TYPE auditaction ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    op.drop_table("record_holds")
    # PostgreSQL does not support removing enum values; they stay.

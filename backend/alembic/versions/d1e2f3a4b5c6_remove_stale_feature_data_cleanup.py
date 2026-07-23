"""remove stale feature: data cleanup + column drops

Revision ID: d1e2f3a4b5c6
Revises: c5d6e7f8a9b0
Create Date: 2026-07-22 00:00:00.000000

"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'c5d6e7f8a9b0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Deliberate, isolated exception to the "audit_logs is append-only /
    # never mutated by application code" convention (see CLAUDE.md). These
    # rows only ever existed because of the stale-checker background job
    # being removed in this same change — there is no material production
    # audit history in this dev/test system yet. Reviewers: this is the one
    # place this repo ever deletes audit rows; reconsider before running
    # this against an environment with a real production audit trail. ---
    op.execute("DELETE FROM audit_logs WHERE action IN ('stale_flagged', 'lock_expired')")

    # tasks.status='stale' rows: remap to 'pending', the closest valid
    # equivalent — these were always still-actionable tasks, and
    # supervisors already have manual reassignment tools (PATCH
    # /tasks/{id}/reassign, POST /tasks/bulk-reassign) to act on them.
    op.execute("UPDATE tasks SET status = 'pending' WHERE status = 'stale'")

    op.drop_column("tasks", "due_at")
    op.drop_column("projects", "stale_threshold_hours")


def downgrade() -> None:
    # Deleted audit rows and the status='stale'->'pending' remap are not
    # recoverable — this is a best-effort structural restore only.
    op.add_column(
        "projects",
        sa.Column("stale_threshold_hours", sa.Float(), nullable=False, server_default="8.0"),
    )
    op.add_column(
        "tasks",
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(op.f("ix_tasks_due_at"), "tasks", ["due_at"])

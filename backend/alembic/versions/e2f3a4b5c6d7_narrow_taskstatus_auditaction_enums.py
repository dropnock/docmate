"""narrow taskstatus and auditaction enums (drop stale-related values)

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-07-22 00:00:01.000000

"""
from typing import Union

from alembic import op

revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- taskstatus: drop 'stale' ---
    # Postgres has no DROP VALUE for enums — rename the old type, create a
    # new one with the reduced value set, cast the column across via text,
    # then drop the old type. Migration d1e2f3a4b5c6 (run just before this
    # one) already remapped every 'stale' row to 'pending', so the cast
    # below never encounters a value the new type doesn't have.
    op.execute("ALTER TYPE taskstatus RENAME TO taskstatus_old")
    op.execute("CREATE TYPE taskstatus AS ENUM ('pending', 'in_progress', 'completed', 'failed')")
    op.execute("ALTER TABLE tasks ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE tasks ALTER COLUMN status TYPE taskstatus USING status::text::taskstatus"
    )
    op.execute("ALTER TABLE tasks ALTER COLUMN status SET DEFAULT 'pending'::taskstatus")
    op.execute("DROP TYPE taskstatus_old")

    # --- auditaction: drop 'stale_flagged', 'lock_expired' ---
    # Migration d1e2f3a4b5c6 already deleted every audit_logs row using
    # either value, so this cast is safe too. No default to preserve on
    # audit_logs.action (nullable=False, no server_default).
    op.execute("ALTER TYPE auditaction RENAME TO auditaction_old")
    op.execute(
        "CREATE TYPE auditaction AS ENUM ("
        "'created','status_changed','locked','unlocked','assigned','reassigned',"
        "'sampled','indexing_submitted','version_created','qa_passed','qa_failed',"
        "'qc_passed','qc_rejected','batch_escalated','deactivated',"
        "'shift_role_changed','disqualified')"
    )
    op.execute(
        "ALTER TABLE audit_logs ALTER COLUMN action TYPE auditaction USING action::text::auditaction"
    )
    op.execute("DROP TYPE auditaction_old")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values, and the prior
    # migration's upgrade already discarded any 'stale'/'stale_flagged'/
    # 'lock_expired' rows — there is nothing meaningful to restore.
    # Structurally, the old wider types could be recreated symmetrically,
    # but since no data can round-trip, this is intentionally a no-op,
    # consistent with every prior enum migration in this repo.
    pass

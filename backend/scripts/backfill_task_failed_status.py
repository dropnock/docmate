"""
One-time backfill: corrects tasks.status for QA/QC rejections that
task_service.fail_task() mis-recorded as `completed` instead of `failed`
(fixed in the same change that introduced this script).

Only the tasks.status column write was ever wrong — audit_service.write_event()
was already logging the true outcome for every historical rejection
(entity_type=task, action=status_changed, new_value={"status": "failed", ...}).
That audit trail is the source of truth this script reconciles the column
against.

Downstream reports (analytics_service.staff_productivity's error_rate,
project_kpis' throughput/burnup) are computed live from tasks.status rather
than stored, so correcting this column retroactively fixes those reports for
historical data too — no separate metrics backfill is needed.

Idempotent and resumable: a task is only touched while its audit trail says
"failed" but its status column still says "completed" — once corrected
(here, or by a fixed fail_task() going forward) it's excluded from future
runs. Only tasks in `completed` are ever rewritten; a task the audit trail
flags as failed but which is currently `pending`/`in_progress` (e.g.
reassigned after the fact) is left alone rather than guessed at.

Dry-run by default: prints what would change and makes no changes. Pass
--confirm to actually apply it.

Run inside the backend container/environment (needs direct DB access):
    docker compose exec backend python -m scripts.backfill_task_failed_status                # dry run
    docker compose exec backend python -m scripts.backfill_task_failed_status --confirm       # apply
    docker compose exec backend python -m scripts.backfill_task_failed_status --tenant-id 3 --confirm
"""
import argparse
import asyncio
import sys

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.models.task import Task, TaskStatus

PAGE_SIZE = 1000
MAX_IDS_TO_PRINT = 50


async def _find_failed_per_audit_task_ids(db: AsyncSession, *, tenant_id: int | None) -> set[int]:
    """Task IDs whose audit trail records a `failed` status_changed event.
    Paged by audit_logs.id rather than one giant query, since the table can
    be large in production."""
    task_ids: set[int] = set()
    last_id = 0
    while True:
        stmt = (
            select(AuditLog.id, AuditLog.entity_id, AuditLog.new_value)
            .where(
                AuditLog.entity_type == AuditEntityType.task,
                AuditLog.action == AuditAction.status_changed,
                AuditLog.id > last_id,
            )
            .order_by(AuditLog.id)
            .limit(PAGE_SIZE)
        )
        if tenant_id is not None:
            stmt = stmt.where(AuditLog.tenant_id == tenant_id)

        rows = (await db.execute(stmt)).all()
        if not rows:
            break
        for log_id, entity_id, new_value in rows:
            last_id = log_id
            if isinstance(new_value, dict) and new_value.get("status") == "failed":
                task_ids.add(entity_id)
        if len(rows) < PAGE_SIZE:
            break
    return task_ids


async def _find_stale_task_ids(db: AsyncSession, *, tenant_id: int | None) -> tuple[list[int], int, int]:
    """Returns (task IDs currently `completed` that the audit trail says were
    actually failed, count already `failed` — i.e. no-ops, likely from a
    prior run of this script — and count in some other status, e.g.
    `pending`/`in_progress` from a reassignment after the fact, which this
    script never touches)."""
    candidate_ids = await _find_failed_per_audit_task_ids(db, tenant_id=tenant_id)
    if not candidate_ids:
        return [], 0, 0

    rows = (await db.execute(
        select(Task.id, Task.status).where(Task.id.in_(candidate_ids))
    )).all()
    stale = sorted(task_id for task_id, status in rows if status == TaskStatus.completed)
    already_correct = sum(1 for _, status in rows if status == TaskStatus.failed)
    other = len(candidate_ids) - len(stale) - already_correct
    return stale, already_correct, other


async def _apply_fix(db: AsyncSession, stale_ids: list[int]) -> None:
    if stale_ids:
        await db.execute(update(Task).where(Task.id.in_(stale_ids)).values(status=TaskStatus.failed))


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tenant-id", type=int, default=None, help="Restrict to one tenant's audit trail.")
    parser.add_argument(
        "--confirm", action="store_true", help="Actually apply the fix. Without this flag, dry run only."
    )
    args = parser.parse_args(argv)

    async with AsyncSessionLocal() as db:
        stale, already_correct, other = await _find_stale_task_ids(db, tenant_id=args.tenant_id)
        total = len(stale) + already_correct + other
        if total == 0:
            print("No task failures found in the audit trail.")
            return 0

        print(f"Audit trail shows {total} task(s) that were actually failed.")
        print(f"  Already correct (status=failed):            {already_correct}")
        print(f"  Miscategorized as completed, need fixing:   {len(stale)}")
        if other:
            print(f"  In another status (pending/in_progress), left alone:  {other}")

        if not stale:
            print("\nNothing to do.")
            return 0

        if len(stale) <= MAX_IDS_TO_PRINT:
            print(f"  Task IDs: {stale}")

        if not args.confirm:
            print("\nDry run only — no changes made. Re-run with --confirm to apply.")
            return 0

        await _apply_fix(db, stale)
        await db.commit()
        print(f"\nCorrected {len(stale)} task(s) to status=failed.")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

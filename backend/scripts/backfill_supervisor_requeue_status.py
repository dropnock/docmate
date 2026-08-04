"""
One-time backfill: corrects records that were sent back for re-indexing
under the pre-fix supervisor-requeue model. record_service.requeue_record
used to set status=qa_failed and parent the record into a dedicated
one-off rework Batch; that batch/task had no assignment UI anywhere (the
digitizing portal's Cabinet Assignment screen only knows how to allocate
*unbatched pending* records, or assign a QA agent to a *qa_review* batch —
neither matched what the old code produced), so those records were
effectively stuck. The current model instead resets status=pending and
detaches the record (batch_id=None) — the same state a never-before-batched
record is in, which Cabinet Assignment's existing "allocate pending records
to indexers" flow already serves correctly. See record_service.
requeue_record's docstring for the full rationale.

Only touches records still sitting exactly in the state the old code left
them in: current status=qa_failed AND current batch_id matches the
batch_id the record's own requeued_for_indexing audit event recorded (its
most recent one, if requeued more than once). A record that has since
moved on by some other path is left untouched.

For each matching record:
  - closes out any still-open (pending/in_progress) Task on it (mirrors
    requeue_record's own task-closing step), so nothing dangling is left
    pointing at the now-detached rework batch
  - resets status=pending, batch_id=None
  - writes a new requeued_for_indexing audit event (attributed to the same
    supervisor who performed the original requeue), documenting the
    correction

The original audit event is left in place untouched — audit_logs is
append-only, and it accurately describes what the code actually did at
that time. The trail ends up showing both what happened and how it was
corrected, rather than rewriting history.

Does not delete the now-empty rework Batch row — left behind as an inert
historical artifact (harmless: an "indexing"-status batch with zero
records; nothing looks it up by content).

Dry-run by default: prints what would change and makes no changes. Pass
--confirm to actually apply it.

Run inside the backend container/environment (needs direct DB access):
    docker compose exec backend python -m scripts.backfill_supervisor_requeue_status
    docker compose exec backend python -m scripts.backfill_supervisor_requeue_status --confirm
    docker compose exec backend python -m scripts.backfill_supervisor_requeue_status --tenant-id 3 --confirm
"""
import argparse
import asyncio
import sys
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.models.record import Record, RecordStatus
from app.models.task import Task, TaskStatus
from app.services import audit_service

MAX_IDS_TO_PRINT = 50


async def _find_stale_records(
    db: AsyncSession, *, tenant_id: int | None
) -> list[tuple[Record, AuditLog]]:
    """Returns (record, its most recent requeued_for_indexing audit event)
    for every record still sitting in the pre-fix state."""
    stmt = select(AuditLog).where(
        AuditLog.entity_type == AuditEntityType.record,
        AuditLog.action == AuditAction.requeued_for_indexing,
    )
    if tenant_id is not None:
        stmt = stmt.where(AuditLog.tenant_id == tenant_id)
    events = (await db.execute(stmt.order_by(AuditLog.id))).scalars().all()

    stale: list[tuple[Record, AuditLog]] = []
    seen_record_ids: set[int] = set()
    # Walk newest-first so a record requeued more than once is checked
    # against its most recent event, not an earlier one.
    for event in reversed(events):
        if event.entity_id in seen_record_ids:
            continue
        seen_record_ids.add(event.entity_id)

        new_value = event.new_value or {}
        if new_value.get("status") != "qa_failed" or "batch_id" not in new_value:
            continue  # already the corrected shape, or something unexpected

        record = await db.get(Record, event.entity_id)
        if not record:
            continue
        if record.status == RecordStatus.qa_failed and record.batch_id == new_value["batch_id"]:
            stale.append((record, event))

    return stale


async def _apply_fix(db: AsyncSession, stale: list[tuple[Record, AuditLog]]) -> None:
    now = datetime.now(timezone.utc)
    for record, original_event in stale:
        tenant_id = original_event.tenant_id

        open_tasks = (await db.execute(
            select(Task).where(
                Task.record_id == record.id,
                Task.status.in_([TaskStatus.pending, TaskStatus.in_progress]),
            )
        )).scalars().all()
        for t in open_tasks:
            prior = t.status.value
            t.status = TaskStatus.failed
            t.completed_at = now
            if t.started_at:
                t.processing_time_seconds = int((now - t.started_at).total_seconds())
            await audit_service.write_event(
                db, tenant_id=tenant_id, entity_type=AuditEntityType.task, entity_id=t.id,
                action=AuditAction.status_changed, performed_by=original_event.performed_by,
                old_value={"status": prior}, new_value={"status": "failed", "reason": "supervisor_requeue"},
            )

        old_status = record.status.value
        record.status = RecordStatus.pending
        record.batch_id = None

        await audit_service.write_event(
            db, tenant_id=tenant_id, entity_type=AuditEntityType.record, entity_id=record.id,
            action=AuditAction.requeued_for_indexing, performed_by=original_event.performed_by,
            old_value={"status": old_status}, new_value={"status": "pending"},
            metadata={
                "note": "Corrected by backfill_supervisor_requeue_status.py — "
                "pending-based model replaces the one-off rework-batch model",
            },
        )


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tenant-id", type=int, default=None, help="Restrict to one tenant.")
    parser.add_argument(
        "--confirm", action="store_true", help="Actually apply the fix. Without this flag, dry run only."
    )
    args = parser.parse_args(argv)

    async with AsyncSessionLocal() as db:
        stale = await _find_stale_records(db, tenant_id=args.tenant_id)
        if not stale:
            print("No records found still sitting in the pre-fix requeued-for-indexing state.")
            return 0

        print(f"{len(stale)} record(s) need correcting (status=qa_failed -> pending, batch detached).")
        ids = [r.id for r, _ in stale]
        if len(ids) <= MAX_IDS_TO_PRINT:
            print(f"  Record IDs: {ids}")

        if not args.confirm:
            print("\nDry run only — no changes made. Re-run with --confirm to apply.")
            return 0

        await _apply_fix(db, stale)
        await db.commit()
        print(f"\nCorrected {len(stale)} record(s).")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

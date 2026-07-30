"""One-time backfill script for pre-fix task rejections
(scripts/backfill_task_failed_status.py).

Tests exercise the internal async helpers directly against the test DB
fixture, the same way test_backfill_tiff_to_pdf.py does — main() opens its
own session via app.core.database.AsyncSessionLocal, which points at
whatever the app is configured against, not the per-test SQLite DB."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.models.task import Task, TaskStatus, TaskType
from scripts import backfill_task_failed_status


async def _make_task(db: AsyncSession, seed, *, status: TaskStatus) -> Task:
    task = Task(
        record_id=seed["record"].id, batch_id=seed["batch"].id,
        task_type=TaskType.qa, assigned_to=seed["qa_staff"].id,
        assigned_by=seed["supervisor"].id, status=status,
    )
    db.add(task)
    await db.flush()
    return task


async def _fail_audit_event(db: AsyncSession, seed, task_id: int) -> None:
    """Mirrors the audit_service.write_event call fail_task() made even
    while the task.status column write next to it was buggy."""
    db.add(AuditLog(
        tenant_id=seed["tenant"].id, entity_type=AuditEntityType.task, entity_id=task_id,
        action=AuditAction.status_changed, performed_by=seed["qa_staff"].id,
        old_value={"status": "in_progress"}, new_value={"status": "failed", "reason": "bad data"},
    ))
    await db.flush()


class TestBackfillTaskFailedStatus:
    async def test_finds_and_fixes_tasks_miscategorized_as_completed(self, db: AsyncSession, seed):
        # Pre-fix: fail_task() left this one `completed` despite its own
        # audit log correctly saying "failed".
        stale_task = await _make_task(db, seed, status=TaskStatus.completed)
        await _fail_audit_event(db, seed, stale_task.id)

        # A task genuinely completed successfully — no failure audit event —
        # must never be touched.
        ok_task = await _make_task(db, seed, status=TaskStatus.completed)

        stale_ids, already_correct, other = await backfill_task_failed_status._find_stale_task_ids(
            db, tenant_id=None
        )
        assert stale_ids == [stale_task.id]
        assert already_correct == 0
        assert other == 0

        await backfill_task_failed_status._apply_fix(db, stale_ids)
        await db.flush()

        await db.refresh(stale_task)
        await db.refresh(ok_task)
        assert stale_task.status == TaskStatus.failed
        assert ok_task.status == TaskStatus.completed

    async def test_idempotent_second_run_finds_nothing_stale(self, db: AsyncSession, seed):
        task = await _make_task(db, seed, status=TaskStatus.completed)
        await _fail_audit_event(db, seed, task.id)

        stale_ids, _, _ = await backfill_task_failed_status._find_stale_task_ids(db, tenant_id=None)
        await backfill_task_failed_status._apply_fix(db, stale_ids)
        await db.flush()

        stale_ids_again, already_correct_again, other_again = (
            await backfill_task_failed_status._find_stale_task_ids(db, tenant_id=None)
        )
        assert stale_ids_again == []
        assert already_correct_again == 1
        assert other_again == 0

    async def test_does_not_touch_task_reassigned_after_failure(self, db: AsyncSession, seed):
        # Audit trail says failed, but the task is currently pending (e.g. a
        # supervisor reassigned it after the fact) — must be left alone
        # rather than guessed at.
        task = await _make_task(db, seed, status=TaskStatus.pending)
        await _fail_audit_event(db, seed, task.id)

        stale_ids, already_correct, other = await backfill_task_failed_status._find_stale_task_ids(
            db, tenant_id=None
        )
        assert stale_ids == []
        assert already_correct == 0
        assert other == 1

        await db.refresh(task)
        assert task.status == TaskStatus.pending

    async def test_tenant_id_scopes_to_one_tenant(self, db: AsyncSession, seed):
        task = await _make_task(db, seed, status=TaskStatus.completed)
        await _fail_audit_event(db, seed, task.id)

        other_tenant_stale, _, _ = await backfill_task_failed_status._find_stale_task_ids(
            db, tenant_id=seed["tenant"].id + 999
        )
        assert other_tenant_stale == []

        this_tenant_stale, _, _ = await backfill_task_failed_status._find_stale_task_ids(
            db, tenant_id=seed["tenant"].id
        )
        assert this_tenant_stale == [task.id]

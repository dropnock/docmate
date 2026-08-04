"""Backfill script correcting records requeued for re-indexing under the
pre-fix supervisor-requeue model (scripts/backfill_supervisor_requeue_status.py).

Tests exercise the internal async helpers directly against the test DB
fixture, the same way test_backfill_task_failed_status.py does — main()
opens its own session via app.core.database.AsyncSessionLocal, which points
at whatever the app is configured against, not the per-test SQLite DB."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Batch, BatchStatus, BatchType, RecordStatus, Task, TaskStatus, TaskType
from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from scripts import backfill_supervisor_requeue_status as backfill


async def _make_stale_record(db: AsyncSession, seed):
    """Reproduces exactly what the pre-fix requeue_record left behind: a
    dedicated rework Batch, the record parented into it at status=
    qa_failed, a dangling pending indexing Task, and the audit event the
    old code wrote."""
    record = seed["record"]
    original_batch_id = record.batch_id

    rework_batch = Batch(
        project_id=seed["project"].id, cabinet_id=None, document_type_id=seed["doc_type"].id,
        name="Supervisor Reindex 1 — Record 1", batch_type=BatchType.indexing,
        status=BatchStatus.indexing,
    )
    db.add(rework_batch)
    await db.flush()

    record.status = RecordStatus.qa_failed
    record.batch_id = rework_batch.id
    await db.flush()

    dangling_task = Task(
        record_id=record.id, batch_id=rework_batch.id, task_type=TaskType.indexing,
        assigned_to=None, assigned_by=seed["supervisor"].id, status=TaskStatus.pending,
    )
    db.add(dangling_task)

    db.add(AuditLog(
        tenant_id=seed["tenant"].id, entity_type=AuditEntityType.record, entity_id=record.id,
        action=AuditAction.requeued_for_indexing, performed_by=seed["supervisor"].id,
        old_value={"status": "qa_passed"},
        new_value={"status": "qa_failed", "batch_id": rework_batch.id},
    ))
    await db.flush()

    return record, rework_batch, dangling_task, original_batch_id


class TestFindStaleRecords:
    async def test_finds_a_record_still_in_the_pre_fix_state(self, db: AsyncSession, seed):
        record, rework_batch, _, _ = await _make_stale_record(db, seed)

        stale = await backfill._find_stale_records(db, tenant_id=None)
        assert [r.id for r, _ in stale] == [record.id]

    async def test_ignores_a_record_that_has_since_moved_on(self, db: AsyncSession, seed):
        record, rework_batch, _, _ = await _make_stale_record(db, seed)
        # Someone worked around the missing UI and actually indexed it.
        record.status = RecordStatus.indexed
        await db.flush()

        stale = await backfill._find_stale_records(db, tenant_id=None)
        assert stale == []

    async def test_tenant_id_scopes_to_one_tenant(self, db: AsyncSession, seed):
        record, _, _, _ = await _make_stale_record(db, seed)

        other_tenant = await backfill._find_stale_records(db, tenant_id=seed["tenant"].id + 999)
        assert other_tenant == []

        this_tenant = await backfill._find_stale_records(db, tenant_id=seed["tenant"].id)
        assert [r.id for r, _ in this_tenant] == [record.id]


class TestApplyFix:
    async def test_resets_status_detaches_batch_closes_task_and_writes_audit(
        self, db: AsyncSession, seed
    ):
        record, rework_batch, dangling_task, original_batch_id = await _make_stale_record(db, seed)

        stale = await backfill._find_stale_records(db, tenant_id=None)
        await backfill._apply_fix(db, stale)
        await db.flush()

        assert record.status == RecordStatus.pending
        assert record.batch_id is None

        await db.refresh(dangling_task)
        assert dangling_task.status == TaskStatus.failed
        assert dangling_task.completed_at is not None

        new_audit = (await db.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.record,
                AuditLog.entity_id == record.id,
                AuditLog.action == AuditAction.requeued_for_indexing,
            ).order_by(AuditLog.id.desc())
        )).scalars().first()
        assert new_audit.old_value == {"status": "qa_failed"}
        assert new_audit.new_value == {"status": "pending"}
        assert new_audit.performed_by == seed["supervisor"].id
        assert "backfill_supervisor_requeue_status" in new_audit.metadata_["note"]

        # Original event is untouched — append-only trail, not rewritten.
        original_still_there = (await db.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.record,
                AuditLog.entity_id == record.id,
                AuditLog.action == AuditAction.requeued_for_indexing,
            )
        )).scalars().all()
        assert len(original_still_there) == 2
        assert any(e.new_value.get("status") == "qa_failed" for e in original_still_there)

    async def test_idempotent_second_run_finds_nothing(self, db: AsyncSession, seed):
        _, _, _, _ = await _make_stale_record(db, seed)

        first_pass = await backfill._find_stale_records(db, tenant_id=None)
        await backfill._apply_fix(db, first_pass)
        await db.flush()

        second_pass = await backfill._find_stale_records(db, tenant_id=None)
        assert second_pass == []

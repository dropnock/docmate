"""scripts.reset_training_qc: a lot the customer has QC'd — even failed,
reworked, resubmitted and sampled again — goes back to released and
unsampled, and the customer can sample and assign it again."""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aql import AQLStatus, SamplingMode
from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.models.batch import Batch, BatchType
from app.models.lot import LotQcRound, LotRecord, LotStatus
from app.models.lot_inspection import LotInspection, LotInspectionStatus
from app.models.qc_field_result import QcFieldResult
from app.models.record import RecordStatus
from app.models.record_version import RecordVersion, VersionReason
from app.models.task import Task, TaskStatus, TaskType
from app.services import lot_service, task_service
from scripts import reset_training_qc as reset
from tests.test_lot_inspections import _make_lot
from tests.test_lot_resubmit import _customer_qc, _inspect_and_release


async def _count(db, model, *where):
    return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


async def _with_v1(db, seed, records):
    """Give each record the v1 snapshot an indexing pass leaves behind."""
    for r in records:
        db.add(RecordVersion(
            record_id=r.id, version_number=1, indexed_data=dict(r.indexed_data),
            created_by=seed["indexer"].id, reason=VersionReason.initial_indexing,
        ))
        r.current_version = 2
    await db.flush()


async def _qa_rework(db, seed, record, data):
    """The QA agent corrects the record the customer rejected."""
    task = (await db.execute(select(Task).where(
        Task.record_id == record.id, Task.task_type == TaskType.qa, Task.status == TaskStatus.pending,
    ))).scalar_one()
    task.assigned_to = seed["qa_staff"].id
    await db.flush()
    await task_service.start_task(db, task_id=task.id, user_id=seed["qa_staff"].id, tenant_id=seed["tenant"].id)
    await task_service.complete_task(
        db, task_id=task.id, user_id=seed["qa_staff"].id, tenant_id=seed["tenant"].id, indexed_data=data,
    )


async def _run(db, seed):
    plan = await reset.plan_reset(db, seed["project"])
    not_restored = await reset.apply_reset(db, plan)
    await db.commit()
    return plan, not_restored


async def test_failed_reworked_resubmitted_lot_goes_back_to_released(db: AsyncSession, seed):
    lot, records = await _make_lot(db, seed, num_records=3)
    await _with_v1(db, seed, records)
    original = {r.id: dict(r.indexed_data) for r in records}
    first_inspection = await _inspect_and_release(db, seed, lot)
    released_at, released_by = lot.released_at, lot.released_by

    # Round 1 fails on a critical field; QA reworks the record; resubmit;
    # round 2 is sampled and assigned.
    sampled = await _customer_qc(db, seed, lot, fail_first=True)
    assert lot.status == LotStatus.failed
    assert seed["aql_config"].current_status == AQLStatus.tightened
    rejected = next(r for r in records if r.id == sampled[0])
    await lot_service.send_for_remediation(db, lot_id=lot.id, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id)
    await _qa_rework(db, seed, rejected, {"surname": "Fixed", "dob": "1999-09-09"})
    await lot_service.resubmit_lot(db, lot_id=lot.id, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id)
    await _inspect_and_release(db, seed, lot)
    await _customer_qc(db, seed, lot, fail_first=False)
    await db.commit()

    plan, not_restored = await _run(db, seed)
    assert not_restored == []
    await db.refresh(lot)
    assert lot.status == LotStatus.released and lot.qc_round == 1
    assert lot.sample_size is None and lot.accuracy_rate is None and lot.qc_completed_at is None
    assert lot.critical_defect_count is None and lot.acceptance_number is None
    # The first release, not the one after the resubmit (SQLite drops the tz).
    assert lot.released_at.replace(tzinfo=None) == released_at.replace(tzinfo=None)
    assert lot.released_by == released_by

    assert await _count(db, Batch, Batch.project_id == seed["project"].id, Batch.batch_type == BatchType.qc) == 0
    assert await _count(db, Batch, Batch.name.like("QC Rework%")) == 0
    assert await _count(db, Task, Task.record_id.in_([r.id for r in records]), Task.task_type != TaskType.qa) == 0
    assert await _count(db, Task, Task.id.in_(plan["task_ids"])) == 0
    assert await _count(db, QcFieldResult) == 0
    assert await _count(db, LotQcRound) == 0
    assert not any((await db.execute(select(LotRecord.is_sampled).where(LotRecord.lot_id == lot.id))).scalars())
    assert plan["batch_ids"]  # something was actually removed

    for r in records:
        await db.refresh(r)
        assert r.status == RecordStatus.qa_passed and r.locked_by is None
        assert r.indexed_data == original[r.id]
        assert r.current_version == 2
    assert await _count(db, RecordVersion, RecordVersion.record_id == rejected.id) == 1

    # The first, passed outgoing inspection counts again; the re-inspection is gone.
    inspections = (await db.execute(select(LotInspection).where(LotInspection.lot_id == lot.id))).scalars().all()
    assert [(i.id, i.status) for i in inspections] == [(first_inspection.id, LotInspectionStatus.passed)]

    # AQL switching as before the first verdict.
    await db.refresh(seed["aql_config"])
    assert seed["aql_config"].current_status == AQLStatus.normal
    assert seed["aql_config"].consecutive_failures == 0 and seed["aql_config"].consecutive_passes == 0

    # Audit: nothing from the first sample onward, the release still there.
    # (The outgoing inspection's own draw is a `sampled` event too — it stays.)
    events = (await db.execute(select(AuditLog).where(
        AuditLog.entity_type == AuditEntityType.lot, AuditLog.entity_id == lot.id,
    ))).scalars().all()
    customer_samples = [
        e for e in events if e.action == AuditAction.sampled and "inspection_id" not in (e.new_value or {})
    ]
    assert customer_samples == []
    assert AuditAction.resubmitted not in {e.action for e in events}
    assert [e.new_value["status"] for e in events if reset._is_release(e)] == ["released"]
    assert await _count(db, AuditLog, AuditLog.entity_type == AuditEntityType.task, AuditLog.entity_id.in_(plan["task_ids"])) == 0

    # The customer can sample and assign it again.
    await _customer_qc(db, seed, lot, fail_first=False)
    assert lot.status == LotStatus.passed


async def test_reworked_record_without_earlier_version_is_reported(db: AsyncSession, seed):
    """Training records are copied in without versions: their pre-QC data
    can't be recovered after rework, and the reset says so."""
    lot, records = await _make_lot(db, seed, num_records=2)
    await _inspect_and_release(db, seed, lot)
    sampled = await _customer_qc(db, seed, lot, fail_first=True)
    rejected = next(r for r in records if r.id == sampled[0])
    await _qa_rework(db, seed, rejected, {"surname": "Fixed", "dob": "1999-09-09"})
    await db.commit()

    _, not_restored = await _run(db, seed)
    assert not_restored == [rejected.id]
    await db.refresh(rejected)
    # No task before QC to say which batch it came from: back to no batch,
    # which is where training copies start.
    assert rejected.status == RecordStatus.qa_passed and rejected.batch_id is None
    assert rejected.current_version == 1
    assert await _count(db, RecordVersion, RecordVersion.record_id == rejected.id) == 0


async def test_unsampled_lots_and_other_work_are_left_alone(db: AsyncSession, seed):
    seed["aql_config"].sampling_mode = SamplingMode.manual
    lot, records = await _make_lot(db, seed, num_records=2)
    await _inspect_and_release(db, seed, lot)
    tasks_before = await _count(db, Task)
    audit_before = await _count(db, AuditLog)
    await db.commit()

    plan, _ = await _run(db, seed)
    assert plan["lots"] == [] and not plan["reset_aql"]
    await db.refresh(lot)
    assert lot.status == LotStatus.released
    assert await _count(db, Task) == tasks_before
    assert await _count(db, AuditLog) == audit_before

import math
import random
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditEntityType
from app.models.batch import Batch, BatchStatus, BatchType
from app.models.lot import Lot, LotRecord, LotStatus
from app.models.record import Record, RecordStatus
from app.models.task import Task, TaskStatus, TaskType
from app.services import audit_service


async def _get_lot(db: AsyncSession, lot_id: int, tenant_id: int) -> Lot:
    lot = await db.get(Lot, lot_id)
    if not lot or lot.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Lot not found")
    return lot


async def create_lot(
    db: AsyncSession,
    *,
    project_id: int,
    name: str,
    description: str | None,
    record_ids: list[int],
    user_id: int,
    tenant_id: int,
) -> Lot:
    # Validate all records are qa_passed
    for rid in record_ids:
        record = await db.get(Record, rid)
        if not record:
            raise HTTPException(status_code=404, detail=f"Record {rid} not found")
        if record.status != RecordStatus.qa_passed:
            raise HTTPException(
                status_code=422,
                detail=f"Record {rid} is not qa_passed (status={record.status})",
            )

    lot = Lot(
        tenant_id=tenant_id,
        project_id=project_id,
        name=name,
        description=description,
        status=LotStatus.draft,
        created_by=user_id,
    )
    db.add(lot)
    await db.flush()

    for rid in record_ids:
        db.add(LotRecord(lot_id=lot.id, record_id=rid, is_sampled=False))

    await db.flush()
    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.record,
        entity_id=lot.id,
        action=AuditAction.created,
        performed_by=user_id,
        new_value={"name": name, "record_count": len(record_ids)},
    )
    return lot


async def list_lots(
    db: AsyncSession,
    *,
    project_id: int,
    tenant_id: int,
) -> list[Lot]:
    result = await db.execute(
        select(Lot).where(
            Lot.project_id == project_id,
            Lot.tenant_id == tenant_id,
        ).order_by(Lot.created_at.desc())
    )
    return list(result.scalars().all())


async def release_lot(
    db: AsyncSession,
    *,
    lot_id: int,
    user_id: int,
    tenant_id: int,
) -> Lot:
    lot = await _get_lot(db, lot_id, tenant_id)
    if lot.status != LotStatus.draft:
        raise HTTPException(status_code=409, detail="Lot must be in draft to release")
    lot.status = LotStatus.released
    lot.released_at = datetime.now(timezone.utc)
    lot.released_by = user_id
    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.record,
        entity_id=lot.id,
        action=AuditAction.status_changed,
        performed_by=user_id,
        old_value={"status": "draft"},
        new_value={"status": "released"},
    )
    return lot


async def apply_sample(
    db: AsyncSession,
    *,
    lot_id: int,
    sample_rate: float,
    user_id: int,
    tenant_id: int,
) -> Lot:
    if not 0 < sample_rate <= 1:
        raise HTTPException(status_code=422, detail="sample_rate must be between 0 and 1")
    lot = await _get_lot(db, lot_id, tenant_id)
    if lot.status != LotStatus.released:
        raise HTTPException(status_code=409, detail="Lot must be released before sampling")

    lot_records_result = await db.execute(
        select(LotRecord).where(LotRecord.lot_id == lot_id)
    )
    all_lot_records = list(lot_records_result.scalars().all())
    total = len(all_lot_records)
    sample_size = max(1, math.ceil(sample_rate * total))
    sampled = random.sample(all_lot_records, min(sample_size, total))

    for lr in sampled:
        lr.is_sampled = True
        record = await db.get(Record, lr.record_id)
        if record:
            record.status = RecordStatus.qc_pending

    lot.sample_rate = sample_rate
    lot.sample_size = sample_size
    lot.status = LotStatus.qc_in_progress

    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.record,
        entity_id=lot.id,
        action=AuditAction.sampled,
        performed_by=user_id,
        new_value={"sample_rate": sample_rate, "sample_size": sample_size, "total": total},
    )
    return lot


async def create_qc_batches(
    db: AsyncSession,
    *,
    lot_id: int,
    project_id: int,
    document_type_id: int,
    assignments: list[dict],  # [{agent_id, record_ids}]
    supervisor_id: int,
    tenant_id: int,
) -> list[Batch]:
    from datetime import timedelta
    from app.models.project import Project

    lot = await _get_lot(db, lot_id, tenant_id)
    if lot.status != LotStatus.qc_in_progress:
        raise HTTPException(status_code=409, detail="Lot must be in qc_in_progress to create QC batches")

    if not assignments:
        raise HTTPException(status_code=400, detail="assignments must not be empty")
    if any(not a["record_ids"] for a in assignments):
        raise HTTPException(status_code=400, detail="each assignment must include at least one record")
    all_record_ids = [rid for a in assignments for rid in a["record_ids"]]
    if len(all_record_ids) != len(set(all_record_ids)):
        raise HTTPException(status_code=400, detail="a record cannot be assigned to more than one QC batch in the same request")

    # A record is only eligible for a new QC batch if it belongs to this lot,
    # is sampled for QC (qc_pending — record.status stays qc_pending for the
    # whole time it's being worked, see task_service.start_task), and has no
    # existing pending/in-progress QC task — otherwise the same record could
    # be silently double-assigned across separate qc-batch requests, the same
    # class of bug fixed for create_indexing_batch above.
    eligible_result = await db.execute(
        select(Record.id)
        .join(LotRecord, LotRecord.record_id == Record.id)
        .where(
            Record.id.in_(all_record_ids),
            LotRecord.lot_id == lot_id,
            Record.status == RecordStatus.qc_pending,
        )
    )
    eligible_ids = set(eligible_result.scalars().all())

    active_qc_result = await db.execute(
        select(Task.record_id).where(
            Task.record_id.in_(all_record_ids),
            Task.task_type == TaskType.qc,
            Task.status.in_([TaskStatus.pending, TaskStatus.in_progress]),
        )
    )
    eligible_ids -= set(active_qc_result.scalars().all())

    ineligible_ids = [rid for rid in all_record_ids if rid not in eligible_ids]
    if ineligible_ids:
        raise HTTPException(
            status_code=400,
            detail=(
                "Records must belong to this lot, be qc_pending, and not already "
                f"assigned to a QC batch: ineligible record ids {ineligible_ids}"
            ),
        )

    project = await db.get(Project, project_id)
    stale_hours = project.stale_threshold_hours if project else 24
    due_at = datetime.now(timezone.utc) + timedelta(hours=stale_hours)

    batches: list[Batch] = []
    for assignment in assignments:
        agent_id = assignment["agent_id"]
        record_ids = assignment["record_ids"]

        batch = Batch(
            project_id=project_id,
            document_type_id=document_type_id,
            name="",
            batch_type=BatchType.qc,
            status=BatchStatus.indexing,
        )
        db.add(batch)
        await db.flush()
        # Name includes the DB-assigned id so it's guaranteed unique even when
        # several QC batches are created in the same request (one per agent
        # in the assignments loop, all within the same wall-clock second).
        batch.name = f"QC Batch {batch.id} — {datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

        for record_id in record_ids:
            task = Task(
                record_id=record_id,
                batch_id=batch.id,
                task_type=TaskType.qc,
                assigned_to=agent_id,
                assigned_by=supervisor_id,
                status=TaskStatus.pending,
                due_at=due_at,
            )
            db.add(task)

        batches.append(batch)

    await db.flush()
    return batches


async def calculate_accuracy(
    db: AsyncSession,
    *,
    lot_id: int,
    tenant_id: int,
) -> Lot:
    """Called after a QC task completes or fails. Checks if all sampled records are done."""
    lot = await _get_lot(db, lot_id, tenant_id)

    sampled_result = await db.execute(
        select(LotRecord).where(LotRecord.lot_id == lot_id, LotRecord.is_sampled == True)  # noqa: E712
    )
    sampled = list(sampled_result.scalars().all())

    statuses = []
    for lr in sampled:
        record = await db.get(Record, lr.record_id)
        if record:
            statuses.append(record.status)

    done_statuses = {RecordStatus.qc_passed, RecordStatus.qc_failed}
    if not all(s in done_statuses for s in statuses):
        return lot  # Not all done yet

    passed = sum(1 for s in statuses if s == RecordStatus.qc_passed)
    total = len(statuses)
    accuracy = passed / total if total else 0.0

    lot.accuracy_rate = accuracy
    lot.status = LotStatus.passed if accuracy >= 0.9 else LotStatus.failed

    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.record,
        entity_id=lot.id,
        action=AuditAction.status_changed,
        performed_by=None,
        new_value={"status": lot.status, "accuracy_rate": accuracy, "passed": passed, "total": total},
    )
    return lot


async def send_for_remediation(
    db: AsyncSession,
    *,
    lot_id: int,
    user_id: int,
    tenant_id: int,
) -> Lot:
    lot = await _get_lot(db, lot_id, tenant_id)
    if lot.status != LotStatus.failed:
        raise HTTPException(status_code=409, detail="Only failed lots can be sent for remediation")
    lot.status = LotStatus.remediation
    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.record,
        entity_id=lot.id,
        action=AuditAction.status_changed,
        performed_by=user_id,
        old_value={"status": "failed"},
        new_value={"status": "remediation"},
    )
    return lot

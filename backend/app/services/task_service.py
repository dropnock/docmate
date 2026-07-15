from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditEntityType
from app.models.batch import Batch, BatchStatus, BatchType
from app.models.project import Project
from app.models.record import Record, RecordStatus
from app.models.task import Task, TaskStatus, TaskType
from app.services import audit_service, staff_assignment_service
from app.services.lock_service import acquire_lock, release_lock


async def _get_stale_hours(db: AsyncSession, batch_id: int) -> float:
    batch = await db.get(Batch, batch_id)
    project = await db.get(Project, batch.project_id)
    return project.stale_threshold_hours


async def assign_task(
    db: AsyncSession,
    *,
    record_id: int,
    batch_id: int,
    task_type: TaskType,
    agent_id: int,
    supervisor_id: int,
    tenant_id: int,
) -> Task:
    stale_hours = await _get_stale_hours(db, batch_id)
    batch = await db.get(Batch, batch_id)
    await staff_assignment_service.require_shift_role_for_task_type(
        db, user_id=agent_id, project_id=batch.project_id, task_type=task_type,
    )
    task = Task(
        record_id=record_id,
        batch_id=batch_id,
        task_type=task_type,
        assigned_to=agent_id,
        assigned_by=supervisor_id,
        status=TaskStatus.pending,
        due_at=datetime.now(timezone.utc) + timedelta(hours=stale_hours),
    )
    db.add(task)
    await db.flush()

    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.task,
        entity_id=task.id,
        action=AuditAction.assigned,
        performed_by=supervisor_id,
        new_value={"agent_id": agent_id, "task_type": task_type.value},
        metadata={"batch_id": batch_id, "record_id": record_id},
    )
    return task


async def start_task(
    db: AsyncSession,
    *,
    task_id: int,
    user_id: int,
    tenant_id: int,
) -> Task:
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.assigned_to != user_id:
        raise HTTPException(status_code=403, detail="Not your task")
    if task.status == TaskStatus.stale:
        raise HTTPException(status_code=409, detail="Task is stale — contact supervisor")

    record = await db.get(Record, task.record_id)
    await acquire_lock(db, record=record, user_id=user_id, tenant_id=tenant_id)

    # Only mark the record as "indexing" for actual indexing tasks.
    # QA and QC tasks leave the record in its current qa_pending / qc_pending state
    # so the status accurately reflects the workflow phase.
    if task.task_type == TaskType.indexing:
        record.status = RecordStatus.indexing
    task.status = TaskStatus.in_progress
    task.started_at = datetime.now(timezone.utc)
    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.task,
        entity_id=task.id,
        action=AuditAction.status_changed,
        performed_by=user_id,
        old_value={"status": "pending"},
        new_value={"status": "in_progress"},
    )
    return task


async def complete_task(
    db: AsyncSession,
    *,
    task_id: int,
    user_id: int,
    tenant_id: int,
    indexed_data: dict | None = None,
) -> Task:
    from app.models.record_version import VersionReason
    from app.services.version_service import create_version

    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.assigned_to != user_id:
        raise HTTPException(status_code=403, detail="Not your task")

    record = await db.get(Record, task.record_id)

    # Persist indexed data and version it for indexing and QA submissions
    if indexed_data is not None and task.task_type in (TaskType.indexing, TaskType.qa):
        record.indexed_data = indexed_data
        reason = (
            VersionReason.initial_indexing
            if record.current_version == 1
            else VersionReason.rework_after_qa
        )
        await create_version(db, record=record, reason=reason, user_id=user_id, tenant_id=tenant_id)
        await audit_service.write_event(
            db,
            tenant_id=tenant_id,
            entity_type=AuditEntityType.record,
            entity_id=record.id,
            action=(
                AuditAction.indexing_submitted
                if task.task_type == TaskType.indexing
                else AuditAction.qa_passed
            ),
            performed_by=user_id,
        )

    await release_lock(db, record=record, user_id=user_id, tenant_id=tenant_id)

    _COMPLETE_STATUS: dict[TaskType, RecordStatus] = {
        TaskType.indexing: RecordStatus.indexed,
        TaskType.qa: RecordStatus.qa_passed,
        TaskType.qc: RecordStatus.qc_passed,
    }
    record.status = _COMPLETE_STATUS.get(task.task_type, RecordStatus.indexed)
    now = datetime.now(timezone.utc)
    task.status = TaskStatus.completed
    task.completed_at = now
    if task.started_at:
        task.processing_time_seconds = int((now - task.started_at).total_seconds())

    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.task,
        entity_id=task.id,
        action=AuditAction.status_changed,
        performed_by=user_id,
        old_value={"status": "in_progress"},
        new_value={"status": "completed"},
    )

    # Auto-advance batch when all records reach terminal indexing/QA state
    await db.flush()
    if task.task_type == TaskType.indexing:
        await _maybe_advance_to_qa(db, batch_id=task.batch_id, tenant_id=tenant_id)
    elif task.task_type == TaskType.qa:
        await _maybe_complete_batch(db, batch_id=task.batch_id, tenant_id=tenant_id)
    elif task.task_type == TaskType.qc:
        await _maybe_finalise_lot(db, record=record, tenant_id=tenant_id)

    return task


async def _maybe_advance_to_qa(db: AsyncSession, *, batch_id: int, tenant_id: int) -> None:
    from sqlalchemy import func
    from app.models.record import RecordStatus
    # Disqualified records are a terminal state that never gets QA'd (see
    # disqualify_task) — they must count as "done" here too, or a single
    # disqualified record would block the batch from ever advancing.
    non_indexed = (await db.execute(
        select(Record).where(
            Record.batch_id == batch_id,
            Record.status.notin_([RecordStatus.indexed, RecordStatus.disqualified]),
        )
    )).scalars().all()
    if not non_indexed:
        from app.services.batch_service import auto_advance_to_qa
        await auto_advance_to_qa(db, batch_id=batch_id, tenant_id=tenant_id)


async def _maybe_complete_batch(db: AsyncSession, *, batch_id: int, tenant_id: int) -> None:
    # auto_advance_to_qa (batch_service.py) skips disqualified records when
    # creating QA tasks, so they never reach qa_passed — treat them as
    # terminal here too, same reasoning as _maybe_advance_to_qa above.
    non_passed = (await db.execute(
        select(Record).where(
            Record.batch_id == batch_id,
            Record.status.notin_([RecordStatus.qa_passed, RecordStatus.disqualified]),
        )
    )).scalars().all()
    if not non_passed:
        from app.services.batch_service import mark_complete
        await mark_complete(db, batch_id=batch_id, tenant_id=tenant_id)


async def _maybe_finalise_lot(db: AsyncSession, *, record: Record, tenant_id: int) -> None:
    from app.models.lot import LotRecord
    lr = (await db.execute(
        select(LotRecord).where(LotRecord.record_id == record.id, LotRecord.is_sampled == True)  # noqa: E712
    )).scalar_one_or_none()
    if lr:
        from app.services.lot_service import calculate_accuracy
        await calculate_accuracy(db, lot_id=lr.lot_id, tenant_id=tenant_id)


async def reassign_task(
    db: AsyncSession,
    *,
    task_id: int,
    new_agent_id: int,
    supervisor_id: int,
    tenant_id: int,
) -> Task:
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    batch = await db.get(Batch, task.batch_id)
    await staff_assignment_service.require_shift_role_for_task_type(
        db, user_id=new_agent_id, project_id=batch.project_id, task_type=task.task_type,
    )

    old_agent = task.assigned_to
    record = await db.get(Record, task.record_id)

    # Release lock if held by old agent
    if record.locked_by == old_agent:
        await release_lock(db, record=record, user_id=old_agent or supervisor_id, tenant_id=tenant_id)

    # Reset stale due_at
    stale_hours = await _get_stale_hours(db, task.batch_id)
    task.assigned_to = new_agent_id
    task.assigned_by = supervisor_id
    task.status = TaskStatus.pending
    task.due_at = datetime.now(timezone.utc) + timedelta(hours=stale_hours)

    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.task,
        entity_id=task.id,
        action=AuditAction.reassigned,
        performed_by=supervisor_id,
        old_value={"agent_id": old_agent},
        new_value={"agent_id": new_agent_id},
    )
    return task


async def bulk_reassign(
    db: AsyncSession,
    *,
    task_ids: list[int],
    new_agent_id: int,
    supervisor_id: int,
    tenant_id: int,
) -> list[Task]:
    return [
        await reassign_task(
            db,
            task_id=tid,
            new_agent_id=new_agent_id,
            supervisor_id=supervisor_id,
            tenant_id=tenant_id,
        )
        for tid in task_ids
    ]


async def get_stale_tasks(
    db: AsyncSession,
    *,
    project_id: int,
    tenant_id: int,
) -> list[Task]:
    result = await db.execute(
        select(Task)
        .join(Batch, Task.batch_id == Batch.id)
        .where(
            Batch.project_id == project_id,
            Task.status.in_([TaskStatus.pending, TaskStatus.in_progress, TaskStatus.stale]),
            Task.due_at <= datetime.now(timezone.utc),
        )
    )
    return list(result.scalars().all())


async def fail_task(
    db: AsyncSession,
    *,
    task_id: int,
    user_id: int,
    reason: str,
    tenant_id: int,
) -> Task:
    """Fail a QA or QC task. QA fail re-queues the record for rework; QC fail logs and checks lot."""
    from app.models.record_version import VersionReason
    from app.services.version_service import create_version

    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.assigned_to != user_id:
        raise HTTPException(status_code=403, detail="Not your task")
    if task.status != TaskStatus.in_progress:
        raise HTTPException(status_code=409, detail="Task is not in progress")

    record = await db.get(Record, task.record_id)
    await release_lock(db, record=record, user_id=user_id, tenant_id=tenant_id)

    now = datetime.now(timezone.utc)
    task.status = TaskStatus.completed
    task.completed_at = now
    if task.started_at:
        task.processing_time_seconds = int((now - task.started_at).total_seconds())

    if task.task_type == TaskType.qa:
        record.status = RecordStatus.qa_failed
        # Snapshot current data as a version so rework history is preserved
        await create_version(
            db, record=record, reason=VersionReason.rework_after_qa,
            user_id=user_id, tenant_id=tenant_id,
        )
        # Create a new indexing task to send back for rework
        stale_hours = await _get_stale_hours(db, task.batch_id)
        db.add(Task(
            record_id=record.id,
            batch_id=task.batch_id,
            task_type=TaskType.indexing,
            assigned_to=None,
            assigned_by=user_id,
            status=TaskStatus.pending,
            due_at=datetime.now(timezone.utc) + timedelta(hours=stale_hours),
        ))
        await audit_service.write_event(
            db, tenant_id=tenant_id, entity_type=AuditEntityType.record, entity_id=record.id,
            action=AuditAction.qa_failed, performed_by=user_id, metadata={"reason": reason},
        )

    elif task.task_type == TaskType.qc:
        record.status = RecordStatus.qc_failed
        await audit_service.write_event(
            db, tenant_id=tenant_id, entity_type=AuditEntityType.record, entity_id=record.id,
            action=AuditAction.qc_rejected, performed_by=user_id, metadata={"reason": reason},
        )

        # Route the record back through QA for remediation. The original
        # batch has typically already completed by the time customer QC
        # rejects it, and assign_qa_agent only ever looks at unassigned QA
        # tasks in a batch whose status is qa_review — so a fresh small
        # batch is created (same convention as create_indexing_batch: new
        # assignable work gets its own batch) to make this record
        # immediately assignable to a QA agent via the existing UI.
        original_batch = await db.get(Batch, task.batch_id)
        stale_hours = await _get_stale_hours(db, task.batch_id)
        rework_batch = Batch(
            project_id=original_batch.project_id,
            cabinet_id=original_batch.cabinet_id,
            document_type_id=original_batch.document_type_id,
            name="",
            batch_type=BatchType.indexing,
            status=BatchStatus.qa_review,
        )
        db.add(rework_batch)
        await db.flush()
        rework_batch.name = f"QC Rework {rework_batch.id} — Record {record.id}"
        record.batch_id = rework_batch.id
        db.add(Task(
            record_id=record.id,
            batch_id=rework_batch.id,
            task_type=TaskType.qa,
            assigned_to=None,
            assigned_by=user_id,
            status=TaskStatus.pending,
            due_at=datetime.now(timezone.utc) + timedelta(hours=stale_hours),
        ))

        await db.flush()
        await _maybe_finalise_lot(db, record=record, tenant_id=tenant_id)

    await audit_service.write_event(
        db, tenant_id=tenant_id, entity_type=AuditEntityType.task, entity_id=task.id,
        action=AuditAction.status_changed, performed_by=user_id,
        old_value={"status": "in_progress"}, new_value={"status": "failed", "reason": reason},
    )
    return task


async def disqualify_task(
    db: AsyncSession,
    *,
    task_id: int,
    user_id: int,
    reason: str,
    tenant_id: int,
) -> Task:
    """An indexer's third option alongside Save Progress / Submit & Complete
    for a record that can't be indexed at all (blank page, wrong document,
    unreadable scan, etc). Skips the schema form entirely — there's no data
    to submit — and marks the record disqualified rather than indexed, a
    terminal state that _maybe_advance_to_qa/_maybe_complete_batch and
    auto_advance_to_qa (batch_service.py) all treat as "done" without
    routing it through QA/QC."""
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.assigned_to != user_id:
        raise HTTPException(status_code=403, detail="Not your task")
    if task.task_type != TaskType.indexing:
        raise HTTPException(status_code=400, detail="Only indexing tasks can be disqualified")

    record = await db.get(Record, task.record_id)
    await release_lock(db, record=record, user_id=user_id, tenant_id=tenant_id)
    record.status = RecordStatus.disqualified

    now = datetime.now(timezone.utc)
    task.status = TaskStatus.completed
    task.completed_at = now
    if task.started_at:
        task.processing_time_seconds = int((now - task.started_at).total_seconds())

    await audit_service.write_event(
        db, tenant_id=tenant_id, entity_type=AuditEntityType.record, entity_id=record.id,
        action=AuditAction.disqualified, performed_by=user_id, metadata={"reason": reason},
    )
    await audit_service.write_event(
        db, tenant_id=tenant_id, entity_type=AuditEntityType.task, entity_id=task.id,
        action=AuditAction.status_changed, performed_by=user_id,
        old_value={"status": "in_progress"}, new_value={"status": "completed", "reason": "disqualified"},
    )

    await db.flush()
    await _maybe_advance_to_qa(db, batch_id=task.batch_id, tenant_id=tenant_id)
    return task

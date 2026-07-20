"""Batch state machine — all transitions are explicit methods."""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.batch import Batch, BatchStatus
from app.models.audit_log import AuditAction, AuditEntityType
from app.models.record import Record, RecordStatus
from app.models.task import Task, TaskStatus, TaskType
from app.services import audit_service, aql_service, staff_assignment_service


async def submit_batch(
    db: AsyncSession,
    *,
    batch_id: int,
    supervisor_id: int,
    tenant_id: int,
) -> Batch:
    batch = await db.get(Batch, batch_id)
    if not batch or batch.status != BatchStatus.draft:
        raise HTTPException(status_code=400, detail="Batch must be in draft to submit")

    # Snapshot current AQL level at submission time
    aql_level = await aql_service.get_current_aql_level(db, batch.project_id)
    batch.aql_level_snapshot = aql_level
    batch.status = BatchStatus.submitted

    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.batch,
        entity_id=batch_id,
        action=AuditAction.status_changed,
        performed_by=supervisor_id,
        old_value={"status": "draft"},
        new_value={"status": "submitted"},
    )
    return batch


async def advance_to_indexing(
    db: AsyncSession,
    *,
    batch_id: int,
    supervisor_id: int,
    tenant_id: int,
) -> Batch:
    batch = await db.get(Batch, batch_id)
    if not batch or batch.status != BatchStatus.submitted:
        raise HTTPException(status_code=400, detail="Batch must be submitted")
    batch.status = BatchStatus.indexing
    await audit_service.write_event(
        db, tenant_id=tenant_id, entity_type=AuditEntityType.batch, entity_id=batch_id,
        action=AuditAction.status_changed, performed_by=supervisor_id,
        old_value={"status": "submitted"}, new_value={"status": "indexing"},
    )
    return batch


async def advance_to_qa(
    db: AsyncSession,
    *,
    batch_id: int,
    supervisor_id: int,
    tenant_id: int,
) -> Batch:
    batch = await db.get(Batch, batch_id)
    if not batch or batch.status != BatchStatus.indexing:
        raise HTTPException(status_code=400, detail="Batch must be in indexing phase")
    batch.status = BatchStatus.qa_review
    await audit_service.write_event(
        db, tenant_id=tenant_id, entity_type=AuditEntityType.batch, entity_id=batch_id,
        action=AuditAction.status_changed, performed_by=supervisor_id,
        old_value={"status": "indexing"}, new_value={"status": "qa_review"},
    )
    return batch


async def advance_to_customer_qc(
    db: AsyncSession,
    *,
    batch_id: int,
    supervisor_id: int,
    tenant_id: int,
) -> Batch:
    """Move to customer_qc and compute AQL sample size."""
    batch = await db.get(Batch, batch_id)
    if not batch or batch.status != BatchStatus.qa_review:
        raise HTTPException(status_code=400, detail="Batch must be in QA review")

    record_count_result = await db.execute(
        select(Record).where(Record.batch_id == batch_id)
    )
    record_count = len(list(record_count_result.scalars().all()))
    aql_level = batch.aql_level_snapshot or 1.5
    sample_size, _ = aql_service.compute_sample_size(record_count, aql_level)
    batch.aql_sample_size = sample_size
    batch.status = BatchStatus.customer_qc

    await audit_service.write_event(
        db, tenant_id=tenant_id, entity_type=AuditEntityType.batch, entity_id=batch_id,
        action=AuditAction.status_changed, performed_by=supervisor_id,
        old_value={"status": "qa_review"}, new_value={"status": "customer_qc"},
        metadata={"aql_sample_size": sample_size},
    )
    return batch


async def record_qc_result(
    db: AsyncSession,
    *,
    batch_id: int,
    defects_found: int,
    performed_by: int,
    tenant_id: int,
) -> dict:
    batch = await db.get(Batch, batch_id)
    if not batch or batch.status != BatchStatus.customer_qc:
        raise HTTPException(status_code=400, detail="Batch must be in customer_qc phase")

    record_count_result = await db.execute(
        select(Record).where(Record.batch_id == batch_id)
    )
    batch_size = len(list(record_count_result.scalars().all()))

    return await aql_service.evaluate_batch(
        db,
        project_id=batch.project_id,
        batch_id=batch_id,
        batch_size=batch_size,
        defects_found=defects_found,
        tenant_id=tenant_id,
        performed_by=performed_by,
    )


async def reject_record_by_customer(
    db: AsyncSession,
    *,
    record_id: int,
    user_id: int,
    tenant_id: int,
    reason: str | None = None,
) -> None:
    """Customer rejects a single record — freeze current data as v1, prep for rework."""
    from app.models.record_version import VersionReason
    from app.services.version_service import create_version

    record = await db.get(Record, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    # Freeze current indexed_data as a version
    await create_version(
        db,
        record=record,
        reason=VersionReason.rework_after_customer_rejection,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    record.status = RecordStatus.qc_failed

    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.record,
        entity_id=record_id,
        action=AuditAction.qc_rejected,
        performed_by=user_id,
        metadata={"reason": reason},
    )


async def complete_indexing_batch(
    db: AsyncSession,
    *,
    batch_id: int,
    user_id: int,
    tenant_id: int,
) -> Batch:
    """Explicit indexer action (the "Complete Batch" button in My Tasks) that
    replaces the old implicit auto-advance-on-last-record behavior. Indexing
    no longer auto-completes the moment every record is indexed/skipped —
    records stay visible and reopenable in My Tasks for correction until the
    indexer confirms the batch here. Blocks with a 400 (rather than silently
    ignoring or completing partially) if anything is still pending/indexing,
    so the indexer gets a clear reason instead of a button that just does
    nothing."""
    from app.models.record import SKIPPED_RECORD_STATUSES

    batch = await db.get(Batch, batch_id)
    if not batch or batch.status != BatchStatus.indexing:
        raise HTTPException(status_code=400, detail="Batch is not in the indexing phase")

    incomplete_result = await db.execute(
        select(Record).where(
            Record.batch_id == batch_id,
            Record.status.notin_([RecordStatus.indexed, *SKIPPED_RECORD_STATUSES]),
        )
    )
    incomplete_count = len(list(incomplete_result.scalars().all()))
    if incomplete_count:
        raise HTTPException(
            status_code=400,
            detail=f"{incomplete_count} record(s) still need to be indexed, withdrawn, "
                   f"ineligible, excluded, lapsed, or illegible before the batch can be completed",
        )

    return await auto_advance_to_qa(
        db, batch_id=batch_id, tenant_id=tenant_id, performed_by=user_id,
    )


async def auto_advance_to_qa(
    db: AsyncSession,
    *,
    batch_id: int,
    tenant_id: int,
    performed_by: int | None = None,
) -> Batch:
    """Moves an indexing batch to qa_review and creates QA tasks. Called from
    complete_indexing_batch below once the indexer has confirmed every
    record is indexed/withdrawn/ineligible/excluded/lapsed/illegible —
    performed_by is theirs.
    performed_by is None only for callers with no attributable user."""
    from app.models.project import Project

    batch = await db.get(Batch, batch_id)
    if not batch or batch.status != BatchStatus.indexing:
        return batch  # Already advanced or wrong state

    batch.status = BatchStatus.qa_review
    await audit_service.write_event(
        db, tenant_id=tenant_id, entity_type=AuditEntityType.batch, entity_id=batch_id,
        action=AuditAction.status_changed, performed_by=performed_by,
        old_value={"status": "indexing"}, new_value={"status": "qa_review"},
    )

    # Create QA tasks for every record in the batch
    project = await db.get(Project, batch.project_id)
    stale_hours = project.stale_threshold_hours if project else 24
    due_at = datetime.now(timezone.utc) + timedelta(hours=stale_hours)

    # Skipped records (withdrawn/ineligible/legacy disqualified — see
    # task_service.skip_task) never get a QA task — there's nothing indexed
    # to review.
    from app.models.record import SKIPPED_RECORD_STATUSES
    records_result = await db.execute(
        select(Record).where(
            Record.batch_id == batch_id,
            Record.status.notin_(SKIPPED_RECORD_STATUSES),
        )
    )
    for record in records_result.scalars().all():
        record.status = RecordStatus.qa_pending
        db.add(Task(
            record_id=record.id,
            batch_id=batch_id,
            task_type=TaskType.qa,
            status=TaskStatus.pending,
            due_at=due_at,
        ))

    await db.flush()
    return batch


async def assign_qa_agent(
    db: AsyncSession,
    *,
    batch_id: int,
    agent_id: int,
    supervisor_id: int,
    tenant_id: int,
) -> Batch:
    """Assign all unassigned QA tasks in the batch to a single QA agent."""
    from app.models.project import Project

    batch = await db.get(Batch, batch_id)
    if not batch or batch.status != BatchStatus.qa_review:
        raise HTTPException(status_code=400, detail="Batch must be in qa_review to assign QA agent")

    await staff_assignment_service.require_shift_role_for_task_type(
        db, user_id=agent_id, project_id=batch.project_id, task_type=TaskType.qa,
    )

    project = await db.get(Project, batch.project_id)
    stale_hours = project.stale_threshold_hours if project else 24
    due_at = datetime.now(timezone.utc) + timedelta(hours=stale_hours)

    tasks_result = await db.execute(
        select(Task).where(
            Task.batch_id == batch_id,
            Task.task_type == TaskType.qa,
            Task.assigned_to == None,  # noqa: E711
        )
    )
    for task in tasks_result.scalars().all():
        task.assigned_to = agent_id
        task.assigned_by = supervisor_id
        task.due_at = due_at

    await audit_service.write_event(
        db, tenant_id=tenant_id, entity_type=AuditEntityType.task, entity_id=batch_id,
        action=AuditAction.assigned, performed_by=supervisor_id,
        new_value={"agent_id": agent_id, "task_type": "qa"},
    )
    return batch


async def mark_complete(
    db: AsyncSession,
    *,
    batch_id: int,
    tenant_id: int,
) -> Batch:
    """Called automatically when all records in a batch are qa_passed."""
    batch = await db.get(Batch, batch_id)
    if not batch:
        return batch
    batch.status = BatchStatus.complete
    await audit_service.write_event(
        db, tenant_id=tenant_id, entity_type=AuditEntityType.batch, entity_id=batch_id,
        action=AuditAction.status_changed, performed_by=None,
        old_value={"status": "qa_review"}, new_value={"status": "complete"},
    )
    return batch

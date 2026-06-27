from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditEntityType
from app.models.batch import Batch
from app.models.project import Project
from app.models.record import RecordStatus
from app.models.task import Task, TaskStatus, TaskType
from app.services import audit_service
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
    task = Task(
        record_id=record_id,
        batch_id=batch_id,
        task_type=task_type,
        assigned_to=agent_id,
        assigned_by=supervisor_id,
        status=TaskStatus.pending,
        due_at=datetime.utcnow() + timedelta(hours=stale_hours),
    )
    db.add(task)
    await db.flush()

    batch = await db.get(Batch, batch_id)
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
    from app.models.record import Record

    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.assigned_to != user_id:
        raise HTTPException(status_code=403, detail="Not your task")
    if task.status == TaskStatus.stale:
        raise HTTPException(status_code=409, detail="Task is stale — contact supervisor")

    record = await db.get(Record, task.record_id)
    await acquire_lock(db, record=record, user_id=user_id, tenant_id=tenant_id)

    task.status = TaskStatus.in_progress
    task.started_at = datetime.utcnow()
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
    from app.models.record import Record
    from app.models.record_version import VersionReason
    from app.services.version_service import create_version

    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.assigned_to != user_id:
        raise HTTPException(status_code=403, detail="Not your task")

    record = await db.get(Record, task.record_id)

    # Persist indexed data and version it (indexing tasks only)
    if indexed_data is not None and task.task_type == TaskType.indexing:
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
            action=AuditAction.indexing_submitted,
            performed_by=user_id,
        )

    await release_lock(db, record=record, user_id=user_id, tenant_id=tenant_id)

    now = datetime.utcnow()
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
    return task


async def reassign_task(
    db: AsyncSession,
    *,
    task_id: int,
    new_agent_id: int,
    supervisor_id: int,
    tenant_id: int,
) -> Task:
    from app.models.record import Record

    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

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
    task.due_at = datetime.utcnow() + timedelta(hours=stale_hours)

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
            Task.due_at <= datetime.utcnow(),
        )
    )
    return list(result.scalars().all())

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import check_project_access, get_current_user, require_roles
from app.models.batch import Batch
from app.models.project import Project
from app.models.task import Task, TaskType
from app.schemas.task import AssignTaskRequest, BulkReassignRequest, CompleteTaskRequest, FailTaskRequest, TaskOut
from app.services import task_service

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


async def _check_batch_access(batch_id: int, db: AsyncSession, current_user) -> None:
    """Same boundary batches.py's router applies before touching a batch —
    task_service/batch_service accept tenant_id only to stamp audit events,
    never as a query filter, so callers must enforce it here."""
    batch = await db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    project = await db.get(Project, batch.project_id)
    check_project_access(project, current_user)


@router.post("/assign", response_model=TaskOut, status_code=201)
async def assign_task(
    body: AssignTaskRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    await _check_batch_access(body.batch_id, db, current_user)
    task = await task_service.assign_task(
        db,
        record_id=body.record_id,
        batch_id=body.batch_id,
        task_type=TaskType(body.task_type),
        agent_id=body.agent_id,
        supervisor_id=current_user.id,
        tenant_id=current_user._tenant_id,
    )
    return task


@router.post("/{task_id}/start", response_model=TaskOut)
async def start_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return await task_service.start_task(
        db, task_id=task_id, user_id=current_user.id, tenant_id=current_user._tenant_id
    )


class TaskFieldResultOut(BaseModel):
    field_key: str
    status: str
    error_type: str | None
    note: str | None


@router.get("/{task_id}/field-results", response_model=list[TaskFieldResultOut])
async def task_field_results(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """The field marks the reviewer gave on this QC task — what the QC
    workspace shows again when they go back to a record they reviewed."""
    from app.models.qc_field_result import QcFieldResult

    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.assigned_to != current_user.id:
        raise HTTPException(status_code=403, detail="Not your task")
    rows = (await db.execute(
        select(QcFieldResult).where(QcFieldResult.task_id == task.id).order_by(QcFieldResult.id)
    )).scalars().all()
    return [
        TaskFieldResultOut(
            field_key=r.field_key, status=r.status.value,
            error_type=r.error_type.value if r.error_type is not None else None,
            note=r.note,
        )
        for r in rows
    ]


@router.post("/{task_id}/heartbeat", status_code=204)
async def heartbeat_task(
    task_id: int,
    opened: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Sent by the workspace while the agent is working on the task, so time
    spent adds up across sessions (services/task_timing.py). opened=true when
    the workspace has just been opened on the task."""
    await task_service.heartbeat_task(db, task_id=task_id, user_id=current_user.id, opened=opened)


@router.post("/{task_id}/complete", response_model=TaskOut)
async def complete_task(
    task_id: int,
    body: CompleteTaskRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return await task_service.complete_task(
        db,
        task_id=task_id,
        user_id=current_user.id,
        tenant_id=current_user._tenant_id,
        indexed_data=body.indexed_data,
        field_results=body.field_results,
    )


@router.patch("/{task_id}/reassign", response_model=TaskOut)
async def reassign_task(
    task_id: int,
    agent_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await _check_batch_access(task.batch_id, db, current_user)
    return await task_service.reassign_task(
        db,
        task_id=task_id,
        new_agent_id=agent_id,
        supervisor_id=current_user.id,
        tenant_id=current_user._tenant_id,
    )


@router.post("/bulk-reassign", response_model=list[TaskOut])
async def bulk_reassign(
    body: BulkReassignRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    tasks_result = await db.execute(select(Task).where(Task.id.in_(body.task_ids)))
    tasks = tasks_result.scalars().all()
    if len(tasks) != len(set(body.task_ids)):
        raise HTTPException(status_code=404, detail="One or more tasks not found")
    for batch_id in {t.batch_id for t in tasks}:
        await _check_batch_access(batch_id, db, current_user)
    return await task_service.bulk_reassign(
        db,
        task_ids=body.task_ids,
        new_agent_id=body.agent_id,
        supervisor_id=current_user.id,
        tenant_id=current_user._tenant_id,
    )


@router.get("/mine", response_model=list[TaskOut])
async def my_tasks(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Tasks assigned to the current user that still need attention: any
    pending/in-progress task (QA/QC unchanged — still disappears from here
    the moment it's completed), plus — for indexing specifically — every
    task whose batch is still in the indexing phase, completed or not. That
    second clause is what keeps an already-indexed/skipped record visible
    and reopenable in My Tasks until the indexer explicitly completes the
    batch (batch_service.complete_indexing_batch); once the batch advances
    past indexing, its tasks drop out of this list the normal way."""
    from sqlalchemy import and_, or_, select
    from app.models.batch import Batch, BatchStatus, BatchType
    from app.models.task import Task, TaskStatus, TaskType

    result = await db.execute(
        select(Task, Batch.status)
        .join(Batch, Task.batch_id == Batch.id)
        .where(
            Task.assigned_to == current_user.id,
            or_(
                Task.status.in_([TaskStatus.pending, TaskStatus.in_progress]),
                and_(Task.task_type == TaskType.indexing, Batch.status == BatchStatus.indexing),
                # Likewise a customer QC reviewer's records stay listed, and
                # can be reviewed again, until they submit the QC batch.
                and_(
                    Task.task_type == TaskType.qc, Batch.batch_type == BatchType.qc,
                    Batch.status == BatchStatus.indexing,
                ),
            ),
        )
    )
    from app.services import flag_service

    tasks = []
    for task, batch_status in result.all():
        task.batch_status = batch_status.value
        tasks.append(task)
    flagged = await flag_service.flagged_record_ids(db, [t.record_id for t in tasks])
    for task in tasks:
        task.flagged = task.record_id in flagged
    return tasks


@router.post("/{task_id}/fail", response_model=TaskOut)
async def fail_task(
    task_id: int,
    body: FailTaskRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return await task_service.fail_task(
        db,
        task_id=task_id,
        user_id=current_user.id,
        reason=body.reason,
        tenant_id=current_user._tenant_id,
        field_results=body.field_results,
    )


class SkipTaskRequest(BaseModel):
    status: Literal["withdrawn", "ineligible", "excluded", "lapsed", "illegible"]


@router.post("/{task_id}/skip", response_model=TaskOut)
async def skip_task(
    task_id: int,
    body: SkipTaskRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from app.models.record import RecordStatus

    return await task_service.skip_task(
        db,
        task_id=task_id,
        user_id=current_user.id,
        status=RecordStatus(body.status),
        tenant_id=current_user._tenant_id,
    )

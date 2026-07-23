from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.models.task import TaskType
from app.schemas.task import AssignTaskRequest, BulkReassignRequest, CompleteTaskRequest, TaskOut
from app.services import task_service

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("/assign", response_model=TaskOut, status_code=201)
async def assign_task(
    body: AssignTaskRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
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
    )


@router.patch("/{task_id}/reassign", response_model=TaskOut)
async def reassign_task(
    task_id: int,
    agent_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
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
    from app.models.batch import Batch, BatchStatus
    from app.models.task import Task, TaskStatus, TaskType

    result = await db.execute(
        select(Task, Batch.status)
        .join(Batch, Task.batch_id == Batch.id)
        .where(
            Task.assigned_to == current_user.id,
            or_(
                Task.status.in_([TaskStatus.pending, TaskStatus.in_progress]),
                and_(Task.task_type == TaskType.indexing, Batch.status == BatchStatus.indexing),
            ),
        )
    )
    tasks = []
    for task, batch_status in result.all():
        task.batch_status = batch_status.value
        tasks.append(task)
    return tasks


from pydantic import BaseModel

class FailTaskRequest(BaseModel):
    reason: str


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

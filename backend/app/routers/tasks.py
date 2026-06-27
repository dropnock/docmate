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
    """Tasks assigned to the current user that are pending or in progress."""
    from sqlalchemy import select
    from app.models.task import Task, TaskStatus

    result = await db.execute(
        select(Task).where(
            Task.assigned_to == current_user.id,
            Task.status.in_([TaskStatus.pending, TaskStatus.in_progress]),
        )
    )
    return list(result.scalars().all())


@router.get("/stale", response_model=list[TaskOut])
async def stale_tasks(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    return await task_service.get_stale_tasks(
        db, project_id=project_id, tenant_id=current_user._tenant_id
    )

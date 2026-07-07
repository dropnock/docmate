from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditEntityType
from app.models.batch import Batch
from app.models.shift import ShiftRole, UserProjectAssignment
from app.models.task import Task, TaskStatus, TaskType
from app.models.user import User
from app.services import audit_service


async def has_active_work(db: AsyncSession, *, user_id: int, project_id: int) -> bool:
    """True if the user currently has any pending/in_progress task on this project."""
    result = await db.execute(
        select(Task.id)
        .join(Batch, Task.batch_id == Batch.id)
        .where(
            Batch.project_id == project_id,
            Task.assigned_to == user_id,
            Task.status.in_([TaskStatus.pending, TaskStatus.in_progress]),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def get_staff_buckets(
    db: AsyncSession,
    *,
    project_id: int,
    shift_id: int,
    tenant_id: int,
) -> dict[str, list[dict]]:
    """Active roster for (project, shift), grouped by shift_role into
    unassigned / indexer / qa buckets, each member flagged with has_active_work."""
    roster_result = await db.execute(
        select(UserProjectAssignment, User)
        .join(User, User.id == UserProjectAssignment.user_id)
        .where(
            UserProjectAssignment.project_id == project_id,
            UserProjectAssignment.shift_id == shift_id,
            UserProjectAssignment.is_active == True,  # noqa: E712
            User.tenant_id == tenant_id,
        )
    )
    rows = roster_result.all()

    active_ids_result = await db.execute(
        select(Task.assigned_to)
        .join(Batch, Task.batch_id == Batch.id)
        .where(
            Batch.project_id == project_id,
            Task.status.in_([TaskStatus.pending, TaskStatus.in_progress]),
            Task.assigned_to.is_not(None),
        )
        .distinct()
    )
    active_user_ids = {row[0] for row in active_ids_result.all()}

    buckets: dict[str, list[dict]] = {"unassigned": [], "indexer": [], "qa": []}
    for assignment, user in rows:
        key = assignment.shift_role.value if assignment.shift_role else "unassigned"
        buckets[key].append({
            "assignment_id": assignment.id,
            "user_id": user.id,
            "full_name": user.full_name,
            "email": user.email,
            "has_active_work": user.id in active_user_ids,
        })
    return buckets


async def move_user_bucket(
    db: AsyncSession,
    *,
    assignment_id: int,
    new_shift_role: ShiftRole | None,
    supervisor_id: int,
    tenant_id: int,
) -> UserProjectAssignment:
    assignment = (await db.execute(
        select(UserProjectAssignment)
        .join(User, User.id == UserProjectAssignment.user_id)
        .where(
            UserProjectAssignment.id == assignment_id,
            User.tenant_id == tenant_id,
        )
    )).scalar_one_or_none()
    if not assignment or not assignment.is_active:
        raise HTTPException(status_code=404, detail="Staff assignment not found")

    if await has_active_work(db, user_id=assignment.user_id, project_id=assignment.project_id):
        raise HTTPException(
            status_code=409,
            detail="This user has an active assigned task on this project and must complete "
                   "or be reassigned off it before being moved to a different role.",
        )

    old_role = assignment.shift_role
    assignment.shift_role = new_shift_role
    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.user,
        entity_id=assignment.user_id,
        action=AuditAction.shift_role_changed,
        performed_by=supervisor_id,
        old_value={
            "shift_role": old_role.value if old_role else None,
            "project_id": assignment.project_id,
            "shift_id": assignment.shift_id,
        },
        new_value={"shift_role": new_shift_role.value if new_shift_role else None},
    )
    return assignment


async def require_shift_role_for_task_type(
    db: AsyncSession,
    *,
    user_id: int,
    project_id: int,
    task_type: TaskType,
) -> None:
    """Raise 400 unless the user currently holds the shift role required for this task type.
    QC tasks are a customer-portal concern and are not gated by indexer/qa buckets."""
    if task_type == TaskType.qc:
        return

    required_role = ShiftRole.indexer if task_type == TaskType.indexing else ShiftRole.qa
    assignment = (await db.execute(
        select(UserProjectAssignment).where(
            UserProjectAssignment.user_id == user_id,
            UserProjectAssignment.project_id == project_id,
            UserProjectAssignment.is_active == True,  # noqa: E712
            UserProjectAssignment.shift_role == required_role,
        )
    )).scalar_one_or_none()
    if not assignment:
        raise HTTPException(
            status_code=400,
            detail=f"Agent is not currently assigned the {required_role.value} role on this project.",
        )

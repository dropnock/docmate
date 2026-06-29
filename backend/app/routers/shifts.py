from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.models.project import Project
from app.models.shift import ProjectShift, Shift, UserProjectAssignment
from app.models.user import User
from app.schemas.shift import (
    AssignShiftToProject,
    AssignStaffToProject,
    AvailableStaffOut,
    ShiftCreate,
    ShiftOut,
    StaffAssignmentOut,
)

router = APIRouter(prefix="/api", tags=["shifts"])


@router.post("/shifts", response_model=ShiftOut, status_code=status.HTTP_201_CREATED)
async def create_shift(
    body: ShiftCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin")),
):
    shift = Shift(
        tenant_id=current_user._tenant_id,
        name=body.name,
        start_time=body.start_time,
        end_time=body.end_time,
        timezone=body.timezone,
    )
    db.add(shift)
    await db.flush()
    return shift


@router.get("/shifts", response_model=list[ShiftOut])
async def list_shifts(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = await db.execute(
        select(Shift).where(Shift.tenant_id == current_user._tenant_id)
    )
    return list(result.scalars().all())


@router.post("/projects/{project_id}/shifts", response_model=StaffAssignmentOut, status_code=201)
async def assign_shift_to_project(
    project_id: int,
    body: AssignShiftToProject,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin")),
):
    project = await db.get(Project, project_id)
    if not project or project.tenant_id != current_user._tenant_id:
        raise HTTPException(status_code=404, detail="Project not found")

    shift = await db.get(Shift, body.shift_id)
    if not shift or shift.tenant_id != current_user._tenant_id:
        raise HTTPException(status_code=404, detail="Shift not found")

    existing = await db.execute(
        select(ProjectShift).where(
            ProjectShift.project_id == project_id,
            ProjectShift.shift_id == body.shift_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Shift already assigned to project")

    ps = ProjectShift(project_id=project_id, shift_id=body.shift_id)
    db.add(ps)
    await db.flush()
    return StaffAssignmentOut(id=ps.id, user_id=0, project_id=project_id, shift_id=body.shift_id, is_active=True)


@router.get("/projects/{project_id}/shifts", response_model=list[ShiftOut])
async def list_project_shifts(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = await db.get(Project, project_id)
    if not project or project.tenant_id != current_user._tenant_id:
        raise HTTPException(status_code=404, detail="Project not found")

    result = await db.execute(
        select(Shift)
        .join(ProjectShift, ProjectShift.shift_id == Shift.id)
        .where(
            ProjectShift.project_id == project_id,
            Shift.tenant_id == current_user._tenant_id,
        )
        .order_by(Shift.name)
    )
    return list(result.scalars().all())


@router.post("/projects/{project_id}/staff", response_model=StaffAssignmentOut, status_code=201)
async def assign_staff_to_project(
    project_id: int,
    body: AssignStaffToProject,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin", "de_supervisor", "customer_supervisor")),
):
    project = await db.get(Project, project_id)
    if not project or project.tenant_id != current_user._tenant_id:
        raise HTTPException(status_code=404, detail="Project not found")

    user = await db.get(User, body.user_id)
    if not user or user.tenant_id != current_user._tenant_id or not user.is_active:
        raise HTTPException(status_code=404, detail="User not found")

    project_shift = await db.execute(
        select(ProjectShift).where(
            ProjectShift.project_id == project_id,
            ProjectShift.shift_id == body.shift_id,
        )
    )
    if not project_shift.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Shift is not assigned to this project")

    existing = await db.execute(
        select(UserProjectAssignment).where(
            UserProjectAssignment.user_id == body.user_id,
            UserProjectAssignment.project_id == project_id,
            UserProjectAssignment.shift_id == body.shift_id,
            UserProjectAssignment.is_active == True,  # noqa: E712
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Staff member already assigned to this project shift")

    assignment = UserProjectAssignment(
        user_id=body.user_id,
        project_id=project_id,
        shift_id=body.shift_id,
        is_active=True,
    )
    db.add(assignment)
    await db.flush()
    return assignment


@router.get("/projects/{project_id}/available-staff", response_model=list[AvailableStaffOut])
async def get_available_staff(
    project_id: int,
    shift_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    """Returns active users assigned to this project on the given shift."""
    result = await db.execute(
        select(User)
        .join(
            UserProjectAssignment,
            (UserProjectAssignment.user_id == User.id)
            & (UserProjectAssignment.project_id == project_id)
            & (UserProjectAssignment.shift_id == shift_id)
            & (UserProjectAssignment.is_active == True),  # noqa: E712
        )
        .where(User.tenant_id == current_user._tenant_id)
    )
    return [
        AvailableStaffOut(
            id=u.id, full_name=u.full_name, email=u.email, role=u.role.value
        )
        for u in result.scalars().all()
    ]

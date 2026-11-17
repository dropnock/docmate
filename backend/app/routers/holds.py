from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import check_project_access, require_roles
from app.models.project import Project
from app.services import hold_service

router = APIRouter(prefix="/api/projects/{project_id}/holds", tags=["holds"])

supervisor = require_roles("de_supervisor", "admin")


class HoldRequest(BaseModel):
    record_ids: list[int]
    reason: str


class ReleaseRequest(BaseModel):
    record_ids: list[int]
    note: str | None = None


async def _project(project_id: int, db: AsyncSession, current_user) -> Project:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    check_project_access(project, current_user)
    return project


@router.get("")
async def list_holds(
    project_id: int,
    active: bool = True,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(supervisor),
):
    """Records on hold (active=true, the default), or holds already cancelled."""
    await _project(project_id, db, current_user)
    return await hold_service.list_holds(
        db, project_id=project_id, tenant_id=current_user._tenant_id, active=active,
    )


@router.post("", status_code=201)
async def hold_records(
    project_id: int,
    body: HoldRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(supervisor),
):
    project = await _project(project_id, db, current_user)
    holds = await hold_service.hold_records(
        db, project=project, record_ids=body.record_ids, reason=body.reason,
        user=current_user, tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return [hold_service.hold_out(h) for h in holds]


@router.post("/release")
async def release_holds(
    project_id: int,
    body: ReleaseRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(supervisor),
):
    project = await _project(project_id, db, current_user)
    holds = await hold_service.release_holds(
        db, project=project, record_ids=body.record_ids, note=body.note,
        user=current_user, tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return [hold_service.hold_out(h) for h in holds]

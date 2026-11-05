from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import check_project_access, get_current_user, require_roles
from app.models.project import Project
from app.models.record import Record
from app.models.record_flag import FlagStatus, RecordFlag
from app.services import flag_service, s3_service

router = APIRouter(prefix="/api", tags=["flags"])


class FlagRaise(BaseModel):
    reason: str


class FlagResolve(BaseModel):
    resolution: str


async def _record(record_id: int, db: AsyncSession, current_user) -> Record:
    record = await db.get(Record, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    check_project_access(await s3_service.resolve_record_project(record, db), current_user)
    return record


@router.get("/records/{record_id}/flag")
async def get_record_flag(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """The record's open flag, if any, and the last resolved one — so the
    indexer sees what the supervisor did about their earlier flag."""
    record = await _record(record_id, db, current_user)
    return {
        "open": flag_service.flag_out(await flag_service.open_flag(db, record.id)),
        "last_resolved": flag_service.flag_out(await flag_service.last_resolved(db, record.id)),
    }


@router.put("/records/{record_id}/flag")
async def raise_record_flag(
    record_id: int,
    body: FlagRaise,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    record = await _record(record_id, db, current_user)
    flag = await flag_service.raise_flag(
        db, record=record, reason=body.reason, user=current_user, tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return flag_service.flag_out(flag)


@router.delete("/records/{record_id}/flag")
async def withdraw_record_flag(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    record = await _record(record_id, db, current_user)
    flag = await flag_service.withdraw_flag(db, record=record, user=current_user, tenant_id=current_user._tenant_id)
    await db.commit()
    return flag_service.flag_out(flag)


@router.get("/projects/{project_id}/flags")
async def list_project_flags(
    project_id: int,
    status: str | None = Query(FlagStatus.open.value, description="open | resolved | withdrawn, or empty for all"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    check_project_access(project, current_user)
    if status and status not in {s.value for s in FlagStatus}:
        raise HTTPException(status_code=422, detail="Unknown flag status")
    return await flag_service.list_flags(db, project_id=project_id, status=status or None)


@router.post("/flags/{flag_id}/resolve")
async def resolve_flag(
    flag_id: int,
    body: FlagResolve,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    flag = await db.get(RecordFlag, flag_id)
    if not flag:
        raise HTTPException(status_code=404, detail="Flag not found")
    check_project_access(await db.get(Project, flag.project_id), current_user)
    flag = await flag_service.resolve_flag(
        db, flag=flag, resolution=body.resolution, user=current_user, tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return flag_service.flag_out(flag)

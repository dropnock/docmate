from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
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


class FlagShare(BaseModel):
    flag_ids: list[int]


class FlagCommentIn(BaseModel):
    body: str


async def _project(project_id: int, db: AsyncSession, current_user) -> Project:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    check_project_access(project, current_user)
    return project


async def _flag(flag_id: int, db: AsyncSession, current_user) -> RecordFlag:
    """The flag, if this user may see it: tenant/project access, and for the
    customer portal only while it's shared (404 otherwise, like any other
    record a customer isn't shown)."""
    flag = await db.get(RecordFlag, flag_id)
    if not flag or flag.tenant_id != current_user._tenant_id:
        raise HTTPException(status_code=404, detail="Flag not found")
    check_project_access(await db.get(Project, flag.project_id), current_user)
    if flag_service.is_customer(current_user) and not flag_service.customer_may_see(flag):
        raise HTTPException(status_code=404, detail="Flag not found")
    return flag


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
    open_flag = await flag_service.open_flag(db, record.id)
    last_resolved = await flag_service.last_resolved(db, record.id)
    if flag_service.is_customer(current_user):
        open_flag = open_flag if open_flag and flag_service.customer_may_see(open_flag) else None
        last_resolved = last_resolved if last_resolved and flag_service.customer_may_see(last_resolved) else None
    return {"open": flag_service.flag_out(open_flag), "last_resolved": flag_service.flag_out(last_resolved)}


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
    q: str | None = Query(None, max_length=200, description="Search record, batch, issue, resolution and messages"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin", "customer_supervisor")),
):
    """A customer supervisor gets only the flags shared with them."""
    await _project(project_id, db, current_user)
    if status and status not in {s.value for s in FlagStatus}:
        raise HTTPException(status_code=422, detail="Unknown flag status")
    return await flag_service.list_flags(
        db, project_id=project_id, status=status or None, shared_only=flag_service.is_customer(current_user), q=q,
    )


@router.get("/projects/{project_id}/flags/export")
async def export_project_flags(
    project_id: int,
    status: str | None = Query(FlagStatus.open.value, description="open | resolved | withdrawn, or empty for all"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    """The flag list as a CSV download — same rows as GET /projects/{id}/flags."""
    await _project(project_id, db, current_user)
    if status and status not in {s.value for s in FlagStatus}:
        raise HTTPException(status_code=422, detail="Unknown flag status")
    body = await flag_service.export_flags_csv(
        db, project_id=project_id, status=status or None, user=current_user, tenant_id=current_user._tenant_id,
    )
    await db.commit()
    filename = f"flagged_records_{project_id}_{status or 'all'}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.csv"
    return Response(
        content=body.encode("utf-8"), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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


@router.get("/flags/{flag_id}")
async def get_flag(
    flag_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin", "customer_supervisor")),
):
    """One flag as a list row — what a notification links to."""
    flag = await _flag(flag_id, db, current_user)
    rows = await flag_service.list_flags(db, project_id=flag.project_id, status=None, flag_id=flag.id)
    return rows[0]


@router.post("/projects/{project_id}/flags/share")
async def share_flags(
    project_id: int,
    body: FlagShare,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    """Makes the selected open flags visible to the customer's supervisors."""
    project = await _project(project_id, db, current_user)
    flags = await flag_service.share_flags(
        db, project=project, flag_ids=body.flag_ids, user=current_user, tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return [flag_service.flag_out(f) for f in flags]


@router.post("/flags/{flag_id}/unshare")
async def unshare_flag(
    flag_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    flag = await _flag(flag_id, db, current_user)
    flag = await flag_service.unshare_flag(db, flag=flag, user=current_user, tenant_id=current_user._tenant_id)
    await db.commit()
    return flag_service.flag_out(flag)


@router.get("/flags/{flag_id}/comments")
async def list_flag_comments(
    flag_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin", "customer_supervisor")),
):
    flag = await _flag(flag_id, db, current_user)
    return await flag_service.list_comments(db, flag)


@router.post("/flags/{flag_id}/comments", status_code=201)
async def add_flag_comment(
    flag_id: int,
    body: FlagCommentIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin", "customer_supervisor")),
):
    flag = await _flag(flag_id, db, current_user)
    comment = await flag_service.add_comment(
        db, flag=flag, body=body.body, user=current_user, tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return comment

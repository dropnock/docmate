"""Record flags — an indexer escalates an issue on a record to a supervisor.

The indexer working a record raises a flag with a note saying what needs
looking at, can edit that note, and can take the flag down again
(withdraw). A digitizing supervisor sees every open flag in the project,
reviews the record and resolves the flag with a note of what was done;
resolving removes the flag. Nothing here touches the record's status or
batch — the record stays where it is whatever the outcome — but an indexing
batch can't be completed (sent on to QA) while one of its records has an
open flag (batch_service.complete_indexing_batch).

Every change is audited against the record, so it shows on its timeline.
Nothing here commits."""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.audit_log import AuditAction, AuditEntityType
from app.models.batch import Batch
from app.models.record import Record
from app.models.record_flag import FlagStatus, RecordFlag
from app.models.task import Task
from app.models.user import User, UserRole
from app.services import audit_service, s3_service

MAX_NOTE = 2000
SUPERVISOR_ROLES = {UserRole.de_supervisor.value, UserRole.admin.value}


def _role(user) -> str:
    return getattr(user.role, "value", user.role)


def _note(text: str | None, what: str) -> str:
    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail=f"Describe the {what}")
    if len(text) > MAX_NOTE:
        raise HTTPException(status_code=422, detail=f"Keep the {what} under {MAX_NOTE} characters")
    return text


async def open_flag(db: AsyncSession, record_id: int) -> RecordFlag | None:
    return (await db.execute(
        select(RecordFlag).where(RecordFlag.record_id == record_id, RecordFlag.status == FlagStatus.open.value)
    )).scalar_one_or_none()


async def last_resolved(db: AsyncSession, record_id: int) -> RecordFlag | None:
    return (await db.execute(
        select(RecordFlag)
        .where(RecordFlag.record_id == record_id, RecordFlag.status == FlagStatus.resolved.value)
        .order_by(RecordFlag.closed_at.desc(), RecordFlag.id.desc())
        .limit(1)
    )).scalar_one_or_none()


async def flagged_record_ids(db: AsyncSession, record_ids: list[int]) -> set[int]:
    if not record_ids:
        return set()
    return set((await db.execute(
        select(RecordFlag.record_id).where(
            RecordFlag.record_id.in_(record_ids), RecordFlag.status == FlagStatus.open.value,
        )
    )).scalars())


async def open_flag_count_in_batch(db: AsyncSession, batch_id: int) -> int:
    return (await db.execute(
        select(func.count(RecordFlag.id))
        .join(Record, Record.id == RecordFlag.record_id)
        .where(Record.batch_id == batch_id, RecordFlag.status == FlagStatus.open.value)
    )).scalar() or 0


async def _check_may_flag(db: AsyncSession, record: Record, user) -> None:
    """The person assigned to work the record (any task of theirs on it), or
    a digitizing supervisor/admin."""
    if _role(user) in SUPERVISOR_ROLES:
        return
    if _role(user) != UserRole.de_staff.value:
        raise HTTPException(status_code=403, detail="Only digitizing staff can flag records")
    mine = (await db.execute(
        select(Task.id).where(Task.record_id == record.id, Task.assigned_to == user.id).limit(1)
    )).scalar_one_or_none()
    if mine is None:
        raise HTTPException(status_code=403, detail="You can only flag records assigned to you")


async def raise_flag(db: AsyncSession, *, record: Record, reason: str, user, tenant_id: int) -> RecordFlag:
    """Flags the record, or updates the note on its open flag."""
    await _check_may_flag(db, record, user)
    reason = _note(reason, "issue")
    flag = await open_flag(db, record.id)
    if flag is not None:
        if flag.reason == reason:
            return flag
        old = flag.reason
        flag.reason = reason
        await audit_service.write_event(
            db, tenant_id=tenant_id, entity_type=AuditEntityType.record, entity_id=record.id,
            action=AuditAction.record_flagged, performed_by=user.id,
            old_value={"reason": old}, new_value={"flag_id": flag.id, "reason": reason},
        )
        return flag

    project = await s3_service.resolve_record_project(record, db)
    flag = RecordFlag(
        tenant_id=tenant_id, project_id=project.id, record_id=record.id, batch_id=record.batch_id,
        status=FlagStatus.open.value, reason=reason, raised_by=user.id,
        raised_at=datetime.now(timezone.utc),
    )
    db.add(flag)
    await db.flush()
    await audit_service.write_event(
        db, tenant_id=tenant_id, entity_type=AuditEntityType.record, entity_id=record.id,
        action=AuditAction.record_flagged, performed_by=user.id,
        new_value={"flag_id": flag.id, "reason": reason, "batch_id": record.batch_id},
    )
    return flag


async def withdraw_flag(db: AsyncSession, *, record: Record, user, tenant_id: int) -> RecordFlag:
    """Takes the open flag down — the person who raised it, or a supervisor."""
    flag = await open_flag(db, record.id)
    if flag is None:
        raise HTTPException(status_code=404, detail="This record isn't flagged")
    if flag.raised_by != user.id and _role(user) not in SUPERVISOR_ROLES:
        raise HTTPException(status_code=403, detail="Only whoever raised the flag can withdraw it")
    flag.status = FlagStatus.withdrawn.value
    flag.closed_by = user.id
    flag.closed_at = datetime.now(timezone.utc)
    await audit_service.write_event(
        db, tenant_id=tenant_id, entity_type=AuditEntityType.record, entity_id=record.id,
        action=AuditAction.flag_withdrawn, performed_by=user.id,
        old_value={"flag_id": flag.id, "reason": flag.reason},
    )
    return flag


async def resolve_flag(db: AsyncSession, *, flag: RecordFlag, resolution: str, user, tenant_id: int) -> RecordFlag:
    """Supervisor closes an open flag with a note of what was done. The
    record is left in its batch, in whatever state it's in."""
    if flag.status != FlagStatus.open.value:
        raise HTTPException(status_code=409, detail=f"This flag is already {flag.status}")
    resolution = _note(resolution, "resolution")
    flag.status = FlagStatus.resolved.value
    flag.resolution = resolution
    flag.closed_by = user.id
    flag.closed_at = datetime.now(timezone.utc)
    await audit_service.write_event(
        db, tenant_id=tenant_id, entity_type=AuditEntityType.record, entity_id=flag.record_id,
        action=AuditAction.flag_resolved, performed_by=user.id,
        old_value={"flag_id": flag.id, "reason": flag.reason}, new_value={"resolution": resolution},
    )
    return flag


async def list_flags(db: AsyncSession, *, project_id: int, status: str | None) -> list[dict]:
    """Flags in a project, newest first, with what a supervisor needs to
    pick one up: the record's filename, its batch, and who raised/closed it."""
    raiser, closer = aliased(User), aliased(User)
    stmt = (
        select(RecordFlag, Record.original_filename, Record.status, Batch.name,
               raiser.full_name, closer.full_name)
        .join(Record, Record.id == RecordFlag.record_id)
        .outerjoin(Batch, Batch.id == Record.batch_id)
        .join(raiser, raiser.id == RecordFlag.raised_by)
        .outerjoin(closer, closer.id == RecordFlag.closed_by)
        .where(RecordFlag.project_id == project_id)
        .order_by(RecordFlag.raised_at.desc(), RecordFlag.id.desc())
    )
    if status:
        stmt = stmt.where(RecordFlag.status == status)
    return [
        {
            **flag_out(flag),
            "record_filename": filename,
            "record_status": getattr(record_status, "value", record_status),
            "batch_name": batch_name,
            "raised_by_name": raised_name,
            "closed_by_name": closed_name,
        }
        for flag, filename, record_status, batch_name, raised_name, closed_name in (await db.execute(stmt)).all()
    ]


def flag_out(flag: RecordFlag | None) -> dict | None:
    if flag is None:
        return None
    return {
        "id": flag.id, "record_id": flag.record_id, "batch_id": flag.batch_id, "project_id": flag.project_id,
        "status": flag.status, "reason": flag.reason,
        "raised_by": flag.raised_by, "raised_at": flag.raised_at,
        "closed_by": flag.closed_by, "closed_at": flag.closed_at, "resolution": flag.resolution,
    }

"""Record holds — a digitizing supervisor holds an unassigned record back so
it isn't assigned, and cancels the hold when it's ready to go.

Only a record nobody is working can be held: it must be pending, in one of
the project's cabinets, and not yet in a batch. While held it can't be put
into an indexing batch (cabinet_service.create_indexing_batch) or given a
task (task_service.assign_task). Holding never changes the record's status.

Every hold and cancellation is audited against the record. Nothing here
commits."""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.audit_log import AuditAction, AuditEntityType
from app.models.cabinet import Cabinet
from app.models.project import Project
from app.models.record import Record, RecordStatus
from app.models.record_hold import RecordHold
from app.models.user import User
from app.services import audit_service

MAX_NOTE = 2000
MAX_RECORDS = 1000


def _text(value: str | None, what: str, *, required: bool) -> str | None:
    value = (value or "").strip()
    if not value:
        if required:
            raise HTTPException(status_code=422, detail=f"Give a {what}")
        return None
    if len(value) > MAX_NOTE:
        raise HTTPException(status_code=422, detail=f"Keep the {what} under {MAX_NOTE} characters")
    return value


def _ids(record_ids: list[int]) -> list[int]:
    ids = list(dict.fromkeys(record_ids))
    if not ids:
        raise HTTPException(status_code=422, detail="Select at least one record")
    if len(ids) > MAX_RECORDS:
        raise HTTPException(status_code=422, detail=f"Select at most {MAX_RECORDS} records at a time")
    return ids


async def active_holds(db: AsyncSession, record_ids: list[int]) -> dict[int, RecordHold]:
    if not record_ids:
        return {}
    rows = (await db.execute(
        select(RecordHold).where(RecordHold.record_id.in_(record_ids), RecordHold.released_at.is_(None))
    )).scalars()
    return {h.record_id: h for h in rows}


async def held_record_ids(db: AsyncSession, record_ids: list[int]) -> set[int]:
    return set(await active_holds(db, record_ids))


async def require_not_held(db: AsyncSession, record_ids: list[int]) -> None:
    """Raises 409 if any of the records is on hold — for the assignment paths."""
    held = sorted(await held_record_ids(db, record_ids))
    if held:
        raise HTTPException(
            status_code=409,
            detail=f"Records are on hold and can't be assigned until the hold is cancelled: record ids {held}",
        )


async def hold_records(
    db: AsyncSession, *, project: Project, record_ids: list[int], reason: str, user, tenant_id: int,
) -> list[RecordHold]:
    reason = _text(reason, "reason for the hold", required=True)
    ids = _ids(record_ids)
    records = {
        r.id: r for r in (await db.execute(
            select(Record).join(Cabinet, Cabinet.id == Record.cabinet_id)
            .where(Record.id.in_(ids), Cabinet.project_id == project.id, Cabinet.tenant_id == tenant_id)
        )).scalars()
    }
    missing = [i for i in ids if i not in records]
    if missing:
        raise HTTPException(status_code=404, detail=f"Records not found in this project: record ids {missing}")
    busy = [i for i in ids if records[i].status != RecordStatus.pending or records[i].batch_id is not None]
    if busy:
        raise HTTPException(
            status_code=409,
            detail=f"Only unassigned records can be held — these are already in a batch or being worked: record ids {busy}",
        )
    already = sorted(await held_record_ids(db, ids))
    if already:
        raise HTTPException(status_code=409, detail=f"Records are already on hold: record ids {already}")

    now = datetime.now(timezone.utc)
    holds = []
    for rid in ids:
        hold = RecordHold(
            tenant_id=tenant_id, project_id=project.id, record_id=rid, reason=reason,
            held_by=user.id, held_at=now,
        )
        db.add(hold)
        holds.append(hold)
    await db.flush()
    for hold in holds:
        await audit_service.write_event(
            db, tenant_id=tenant_id, entity_type=AuditEntityType.record, entity_id=hold.record_id,
            action=AuditAction.record_held, performed_by=user.id,
            new_value={"hold_id": hold.id, "reason": reason},
        )
    return holds


async def release_holds(
    db: AsyncSession, *, project: Project, record_ids: list[int], note: str | None, user, tenant_id: int,
) -> list[RecordHold]:
    note = _text(note, "note", required=False)
    ids = _ids(record_ids)
    holds = await active_holds(db, ids)
    missing = [i for i in ids if i not in holds or holds[i].project_id != project.id or holds[i].tenant_id != tenant_id]
    if missing:
        raise HTTPException(status_code=404, detail=f"These records aren't on hold in this project: record ids {missing}")
    now = datetime.now(timezone.utc)
    for rid in ids:
        hold = holds[rid]
        hold.released_by = user.id
        hold.released_at = now
        hold.release_note = note
        await audit_service.write_event(
            db, tenant_id=tenant_id, entity_type=AuditEntityType.record, entity_id=rid,
            action=AuditAction.hold_released, performed_by=user.id,
            old_value={"hold_id": hold.id, "reason": hold.reason},
            new_value={"note": note} if note else None,
        )
    return [holds[i] for i in ids]


async def list_holds(db: AsyncSession, *, project_id: int, tenant_id: int, active: bool) -> list[dict]:
    """The project's holds, newest first — active ones, or cancelled ones."""
    holder, releaser = aliased(User), aliased(User)
    stmt = (
        select(RecordHold, Record.original_filename, Record.source_identifier, Record.status,
               holder.full_name, releaser.full_name)
        .join(Record, Record.id == RecordHold.record_id)
        .join(holder, holder.id == RecordHold.held_by)
        .outerjoin(releaser, releaser.id == RecordHold.released_by)
        .where(RecordHold.project_id == project_id, RecordHold.tenant_id == tenant_id)
        .where(RecordHold.released_at.is_(None) if active else RecordHold.released_at.is_not(None))
        .order_by(RecordHold.held_at.desc(), RecordHold.id.desc())
    )
    return [
        {
            **hold_out(hold),
            "record_filename": filename,
            "source_identifier": source_identifier,
            "record_status": getattr(status, "value", status),
            "held_by_name": held_name,
            "released_by_name": released_name,
        }
        for hold, filename, source_identifier, status, held_name, released_name in (await db.execute(stmt)).all()
    ]


def hold_out(hold: RecordHold) -> dict:
    return {
        "id": hold.id, "record_id": hold.record_id, "project_id": hold.project_id, "reason": hold.reason,
        "held_by": hold.held_by, "held_at": hold.held_at,
        "released_by": hold.released_by, "released_at": hold.released_at, "release_note": hold.release_note,
    }

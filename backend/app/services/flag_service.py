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
import csv
import io
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.audit_log import AuditAction, AuditEntityType
from app.models.batch import Batch
from app.models.record import Record
from app.models.notification import NotificationKind
from app.models.project import Project
from app.models.record_flag import FlagComment, FlagStatus, RecordFlag
from app.models.task import Task
from app.models.user import Portal, User, UserRole
from app.services import audit_service, notification_service, s3_service

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
    if flag.shared_at is not None:
        name = (await _record_names(db, [flag.record_id]))[flag.record_id]
        await notification_service.notify_project_supervisors(
            db, project=await db.get(Project, flag.project_id), kind=NotificationKind.flag_resolved,
            message=f"{user.full_name} withdrew the flag on {name}",
            actor_id=user.id, flag_id=flag.id, record_id=flag.record_id,
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
    if flag.shared_at is not None:
        name = (await _record_names(db, [flag.record_id]))[flag.record_id]
        await notification_service.notify_project_supervisors(
            db, project=await db.get(Project, flag.project_id), kind=NotificationKind.flag_resolved,
            message=f"{user.full_name} resolved the flag on {name}: {_excerpt(resolution)}",
            actor_id=user.id, flag_id=flag.id, record_id=flag.record_id,
        )
    return flag


async def list_flags(
    db: AsyncSession, *, project_id: int, status: str | None, shared_only: bool = False, flag_id: int | None = None,
) -> list[dict]:
    """Flags in a project, newest first, with what a supervisor needs to
    pick one up: the record's filename, its batch, who raised/closed it,
    whether it's shared with the customer and how long its discussion is.
    shared_only is what a customer supervisor gets."""
    raiser, closer, sharer = aliased(User), aliased(User), aliased(User)
    comments = (
        select(func.count(FlagComment.id)).where(FlagComment.flag_id == RecordFlag.id).scalar_subquery()
    )
    stmt = (
        select(RecordFlag, Record.original_filename, Record.source_identifier, Record.status, Batch.name,
               raiser.full_name, closer.full_name, sharer.full_name, comments)
        .join(Record, Record.id == RecordFlag.record_id)
        .outerjoin(Batch, Batch.id == Record.batch_id)
        .join(raiser, raiser.id == RecordFlag.raised_by)
        .outerjoin(closer, closer.id == RecordFlag.closed_by)
        .outerjoin(sharer, sharer.id == RecordFlag.shared_by)
        .where(RecordFlag.project_id == project_id)
        .order_by(RecordFlag.raised_at.desc(), RecordFlag.id.desc())
    )
    if status:
        stmt = stmt.where(RecordFlag.status == status)
    if shared_only:
        stmt = stmt.where(RecordFlag.shared_at.is_not(None))
    if flag_id is not None:
        stmt = stmt.where(RecordFlag.id == flag_id)
    return [
        {
            **flag_out(flag),
            "record_filename": filename,
            "source_identifier": source_identifier,
            "record_status": getattr(record_status, "value", record_status),
            "batch_name": batch_name,
            "raised_by_name": raised_name,
            "closed_by_name": closed_name,
            "shared_by_name": shared_name,
            "comment_count": comment_count or 0,
        }
        for flag, filename, source_identifier, record_status, batch_name, raised_name, closed_name, shared_name,
        comment_count in (await db.execute(stmt)).all()
    ]


CSV_COLUMNS = [
    ("Flag ID", "id"), ("Record ID", "record_id"), ("Record", "record_filename"),
    ("Identifier", "source_identifier"), ("Batch", "batch_name"), ("Record status", "record_status"),
    ("Flag status", "status"), ("Issue", "reason"), ("Flagged by", "raised_by_name"),
    ("Flagged at (UTC)", "raised_at"), ("Closed by", "closed_by_name"), ("Closed at (UTC)", "closed_at"),
    ("Resolution", "resolution"), ("Shared with customer (UTC)", "shared_at"), ("Shared by", "shared_by_name"),
    ("Discussion", "discussion"),
]


def _csv_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if value.tzinfo else value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value)
    # Free text comes from users: keep a spreadsheet from running it as a formula.
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        text = "'" + text
    return text


async def export_flags_csv(db: AsyncSession, *, project_id: int, status: str | None, user, tenant_id: int) -> str:
    """The flag list as CSV (UTF-8 with a BOM so Excel reads accents), one
    row per flag. The export itself is audited against the project."""
    flags = await list_flags(db, project_id=project_id, status=status)
    threads: dict[int, list[str]] = {}
    for c in await _comments(db, [f["id"] for f in flags]):
        threads.setdefault(c["flag_id"], []).append(
            f"[{_csv_cell(c['created_at'])}] {c['author_name']} ({c['author_side']}): {c['body']}"
        )
    for f in flags:
        f["discussion"] = "\n".join(threads.get(f["id"], []))
    out = io.StringIO()
    out.write("\ufeff")
    writer = csv.writer(out)
    writer.writerow([title for title, _ in CSV_COLUMNS])
    for f in flags:
        writer.writerow([_csv_cell(f[key]) for _, key in CSV_COLUMNS])
    await audit_service.write_event(
        db, tenant_id=tenant_id, entity_type=AuditEntityType.project, entity_id=project_id,
        action=AuditAction.flags_exported, performed_by=user.id,
        new_value={"status": status or "all", "rows": len(flags)},
    )
    return out.getvalue()


def flag_out(flag: RecordFlag | None) -> dict | None:
    if flag is None:
        return None
    return {
        "id": flag.id, "record_id": flag.record_id, "batch_id": flag.batch_id, "project_id": flag.project_id,
        "status": flag.status, "reason": flag.reason,
        "raised_by": flag.raised_by, "raised_at": flag.raised_at,
        "closed_by": flag.closed_by, "closed_at": flag.closed_at, "resolution": flag.resolution,
        "shared_at": flag.shared_at, "shared_by": flag.shared_by,
    }


# ---- sharing with the customer, and the discussion on a shared flag ----

MAX_SHARE = 500


def is_customer(user) -> bool:
    return getattr(user.portal, "value", user.portal) == Portal.customer.value


def customer_may_see(flag: RecordFlag) -> bool:
    """A customer supervisor only ever sees flags shared with them."""
    return flag.shared_at is not None


def _excerpt(text: str, limit: int = 140) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def _record_names(db: AsyncSession, record_ids: list[int]) -> dict[int, str]:
    rows = (await db.execute(
        select(Record.id, Record.original_filename, Record.source_identifier).where(Record.id.in_(record_ids))
    )).all()
    return {rid: filename or ident or f"record #{rid}" for rid, filename, ident in rows}


async def share_flags(
    db: AsyncSession, *, project: Project, flag_ids: list[int], user, tenant_id: int,
) -> list[RecordFlag]:
    """Makes open flags visible to the project's customer supervisors so they
    can advise on the record. Already-shared flags are left as they are."""
    ids = list(dict.fromkeys(flag_ids))
    if not ids:
        raise HTTPException(status_code=422, detail="Select at least one flag")
    if len(ids) > MAX_SHARE:
        raise HTTPException(status_code=422, detail=f"Select at most {MAX_SHARE} flags at a time")
    flags = {
        f.id: f for f in (await db.execute(
            select(RecordFlag).where(
                RecordFlag.id.in_(ids), RecordFlag.project_id == project.id, RecordFlag.tenant_id == tenant_id,
            )
        )).scalars()
    }
    missing = [i for i in ids if i not in flags]
    if missing:
        raise HTTPException(status_code=404, detail=f"Flags not found in this project: flag ids {missing}")
    closed = [i for i in ids if flags[i].status != FlagStatus.open.value]
    if closed:
        raise HTTPException(status_code=409, detail=f"Only open flags can be shared: flag ids {closed}")

    newly = [flags[i] for i in ids if flags[i].shared_at is None]
    now = datetime.now(timezone.utc)
    for flag in newly:
        flag.shared_at = now
        flag.shared_by = user.id
        await audit_service.write_event(
            db, tenant_id=tenant_id, entity_type=AuditEntityType.record, entity_id=flag.record_id,
            action=AuditAction.flag_shared, performed_by=user.id,
            new_value={"flag_id": flag.id, "reason": flag.reason},
        )
    if len(newly) == 1:
        flag = newly[0]
        name = (await _record_names(db, [flag.record_id]))[flag.record_id]
        await notification_service.notify_project_supervisors(
            db, project=project, kind=NotificationKind.flag_shared,
            message=f"{user.full_name} shared the flag on {name} with the customer: {_excerpt(flag.reason)}",
            actor_id=user.id, flag_id=flag.id, record_id=flag.record_id,
        )
    elif newly:
        await notification_service.notify_project_supervisors(
            db, project=project, kind=NotificationKind.flag_shared,
            message=f"{user.full_name} shared {len(newly)} flagged records with the customer",
            actor_id=user.id,
        )
    return [flags[i] for i in ids]


async def unshare_flag(db: AsyncSession, *, flag: RecordFlag, user, tenant_id: int) -> RecordFlag:
    """Hides the flag (and its discussion) from the customer again."""
    if flag.shared_at is None:
        raise HTTPException(status_code=409, detail="This flag isn't shared with the customer")
    flag.shared_at = None
    flag.shared_by = None
    await audit_service.write_event(
        db, tenant_id=tenant_id, entity_type=AuditEntityType.record, entity_id=flag.record_id,
        action=AuditAction.flag_unshared, performed_by=user.id, old_value={"flag_id": flag.id},
    )
    name = (await _record_names(db, [flag.record_id]))[flag.record_id]
    # No link to the flag: the customer can't open it any more.
    await notification_service.notify_project_supervisors(
        db, project=await db.get(Project, flag.project_id), kind=NotificationKind.flag_unshared,
        message=f"{user.full_name} stopped sharing the flag on {name} with the customer",
        actor_id=user.id,
    )
    return flag


async def add_comment(db: AsyncSession, *, flag: RecordFlag, body: str, user, tenant_id: int) -> dict:
    """A digitizing or customer supervisor adds to a shared, open flag's
    discussion; everyone else supervising the project is notified."""
    if flag.shared_at is None:
        raise HTTPException(status_code=409, detail="Share the flag with the customer before discussing it")
    if flag.status != FlagStatus.open.value:
        raise HTTPException(status_code=409, detail=f"This flag is {flag.status} — its discussion is closed")
    body = (body or "").strip()
    if not body:
        raise HTTPException(status_code=422, detail="Write a message")
    if len(body) > MAX_NOTE:
        raise HTTPException(status_code=422, detail=f"Keep the message under {MAX_NOTE} characters")
    comment = FlagComment(
        tenant_id=tenant_id, flag_id=flag.id, author_id=user.id, body=body, created_at=datetime.now(timezone.utc),
    )
    db.add(comment)
    await db.flush()
    await audit_service.write_event(
        db, tenant_id=tenant_id, entity_type=AuditEntityType.record, entity_id=flag.record_id,
        action=AuditAction.flag_comment_added, performed_by=user.id,
        new_value={"flag_id": flag.id, "comment_id": comment.id, "body": body},
    )
    name = (await _record_names(db, [flag.record_id]))[flag.record_id]
    await notification_service.notify_project_supervisors(
        db, project=await db.get(Project, flag.project_id), kind=NotificationKind.flag_comment,
        message=f"{user.full_name} on {name}: {_excerpt(body)}",
        actor_id=user.id, flag_id=flag.id, record_id=flag.record_id,
    )
    return (await _comments(db, [flag.id], comment_id=comment.id))[0]


async def _comments(db: AsyncSession, flag_ids: list[int], comment_id: int | None = None) -> list[dict]:
    if not flag_ids:
        return []
    stmt = (
        select(FlagComment, User.full_name, User.portal)
        .join(User, User.id == FlagComment.author_id)
        .where(FlagComment.flag_id.in_(flag_ids))
        .order_by(FlagComment.created_at, FlagComment.id)
    )
    if comment_id is not None:
        stmt = stmt.where(FlagComment.id == comment_id)
    return [
        {
            "id": c.id, "flag_id": c.flag_id, "author_id": c.author_id, "author_name": name,
            "author_side": "customer" if getattr(portal, "value", portal) == Portal.customer.value else "digitizing",
            "body": c.body, "created_at": c.created_at,
        }
        for c, name, portal in (await db.execute(stmt)).all()
    ]


async def list_comments(db: AsyncSession, flag: RecordFlag) -> list[dict]:
    return await _comments(db, [flag.id])


FLAG_EVENT_ACTIONS = {
    AuditAction.record_flagged, AuditAction.flag_withdrawn, AuditAction.flag_resolved,
    AuditAction.flag_shared, AuditAction.flag_unshared, AuditAction.flag_comment_added,
}


async def shared_flag_ids_for_record(db: AsyncSession, record_id: int) -> set[int]:
    return set((await db.execute(
        select(RecordFlag.id).where(RecordFlag.record_id == record_id, RecordFlag.shared_at.is_not(None))
    )).scalars())


def is_hidden_flag_event(event, shared_flag_ids: set[int]) -> bool:
    """For a customer's view of a record's history: a flag event is hidden
    unless it belongs to a flag currently shared with them."""
    if event.action not in FLAG_EVENT_ACTIONS:
        return False
    flag_id = (event.new_value or {}).get("flag_id") or (event.old_value or {}).get("flag_id")
    return flag_id not in shared_flag_ids

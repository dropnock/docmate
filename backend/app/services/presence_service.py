"""Who's online: every authenticated request stamps the user's last_seen_at
(at most once a minute, so a busy client doesn't write the users row on
every call). A gap longer than SESSION_GAP starts a new run of activity.

"Last seen" means a signed-in browser tab was talking to the API — the
portals poll, so an idle open tab still counts. Whether someone is actually
working shows on their open task: task_timing only heartbeats while there
was input in the last few minutes.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

from app.models.batch import Batch
from app.models.organization import Organization
from app.models.project import Project
from app.models.record import Record
from app.models.task import Task, TaskStatus
from app.models.user import User

logger = logging.getLogger(__name__)

# Stamp in a separate, immediately committed transaction so the users row
# isn't held locked for the rest of the request (a long upload or export
# would otherwise stall that user's other requests). Tests switch this off
# and stamp inside their shared session.
USE_OWN_SESSION = True

TOUCH_INTERVAL = timedelta(seconds=60)
SESSION_GAP = timedelta(minutes=30)  # the doc realm's SSO idle timeout
ACTIVE_WINDOW = timedelta(minutes=2)
WORKING_WINDOW = timedelta(seconds=90)  # task_timing.MAX_GAP_SECONDS


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _due(user: User, now: datetime) -> bool:
    last = _utc(user.last_seen_at)
    return last is None or now - last >= TOUCH_INTERVAL


async def record_request(db: AsyncSession, user: User) -> None:
    """Called by get_current_user on every authenticated request. Never
    fails the request: presence is best effort."""
    now = datetime.now(timezone.utc)
    if not _due(user, now):
        return
    if not USE_OWN_SESSION:
        await touch(db, user, now)
        return
    from app.core.database import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as own:
            await touch(own, user, now)
            await own.commit()
    except Exception as exc:  # noqa: BLE001 — presence must not break auth
        logger.warning("Could not record presence for user %s: %s", user.id, exc)


async def touch(db: AsyncSession, user: User, now: datetime | None = None) -> None:
    """Record a request by user. Written with a Core UPDATE that keeps
    updated_at as it was — presence is not an edit to the account."""
    now = now or datetime.now(timezone.utc)
    if not _due(user, now):
        return
    last = _utc(user.last_seen_at)
    started = _utc(user.session_started_at)
    if last is None or started is None or now - last > SESSION_GAP:
        started = now
    await db.execute(
        update(User)
        .where(User.id == user.id)
        .values(last_seen_at=now, session_started_at=started, updated_at=User.updated_at)
        .execution_options(synchronize_session=False)
    )
    # Keep the loaded object in step without marking it dirty.
    attributes.set_committed_value(user, "last_seen_at", now)
    attributes.set_committed_value(user, "session_started_at", started)


async def online_users(
    db: AsyncSession, *, tenant_id: int, within: timedelta, now: datetime | None = None
) -> list[dict]:
    """Users seen in the last `within`, most recent first, each with the
    task they have open (if any)."""
    now = now or datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(User, Organization.name)
            .outerjoin(Organization, Organization.id == User.organization_id)
            .where(
                User.tenant_id == tenant_id,
                User.is_active.is_(True),
                User.last_seen_at.is_not(None),
                User.last_seen_at >= now - within,
            )
            .order_by(User.last_seen_at.desc())
        )
    ).all()
    if not rows:
        return []

    user_ids = [u.id for u, _ in rows]
    task_rows = (
        await db.execute(
            select(Task, Record, Batch.name, Project.name)
            .join(Record, Record.id == Task.record_id)
            .join(Batch, Batch.id == Task.batch_id)
            .join(Project, Project.id == Batch.project_id)
            .where(Task.assigned_to.in_(user_ids), Task.status == TaskStatus.in_progress)
        )
    ).all()
    # Several in-progress tasks: show the one touched most recently.
    open_task: dict[int, dict] = {}
    for task, record, batch_name, project_name in task_rows:
        active = _utc(task.last_active_at) or _utc(task.started_at)
        prev = open_task.get(task.assigned_to)
        if prev and (active is None or (prev["last_active_at"] and prev["last_active_at"] >= active)):
            continue
        open_task[task.assigned_to] = {
            "id": task.id,
            "task_type": task.task_type.value,
            "record_id": record.id,
            "record_name": record.original_filename or record.source_identifier or f"Record #{record.id}",
            "batch_name": batch_name,
            "project_name": project_name,
            "last_active_at": active,
            "working": active is not None and now - active <= WORKING_WINDOW,
        }

    out = []
    for user, org_name in rows:
        seen = _utc(user.last_seen_at)
        out.append({
            "id": user.id,
            "full_name": user.full_name,
            "email": user.email,
            "role": user.role.value,
            "portal": user.portal.value,
            "organization_name": org_name,
            "last_seen_at": seen,
            "session_started_at": _utc(user.session_started_at),
            "status": "active" if now - seen <= ACTIVE_WINDOW else "idle",
            "task": open_task.get(user.id),
        })
    return out

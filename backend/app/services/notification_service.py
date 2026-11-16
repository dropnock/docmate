"""In-app notifications (the header bell in both portals).

notify_project_supervisors() writes one Notification per recipient in the
caller's transaction, so a notice exists exactly when the change it reports
was committed. Recipients are the project's supervisors on both sides:
active digitizing supervisors of its digitizing organisation and active
customer supervisors of its customer organisation, minus whoever made the
change. Nothing here commits."""
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationKind
from app.models.project import Project
from app.models.user import User, UserRole

LIST_LIMIT = 50


async def project_supervisors(db: AsyncSession, project: Project) -> list[User]:
    return list((await db.execute(
        select(User).where(
            User.tenant_id == project.tenant_id,
            User.is_active.is_(True),
            or_(
                and_(
                    User.role == UserRole.de_supervisor,
                    or_(User.organization_id == project.digitizing_org_id, User.organization_id.is_(None)),
                ),
                and_(User.role == UserRole.customer_supervisor, User.organization_id == project.customer_org_id),
            ),
        ).order_by(User.id)
    )).scalars())


async def notify_project_supervisors(
    db: AsyncSession,
    *,
    project: Project,
    kind: NotificationKind,
    message: str,
    actor_id: int | None,
    flag_id: int | None = None,
    record_id: int | None = None,
) -> list[Notification]:
    notes = [
        Notification(
            tenant_id=project.tenant_id, user_id=user.id, kind=kind.value, project_id=project.id,
            flag_id=flag_id, record_id=record_id, actor_id=actor_id, message=message,
            created_at=datetime.now(timezone.utc),
        )
        for user in await project_supervisors(db, project)
        if user.id != actor_id
    ]
    db.add_all(notes)
    return notes


async def list_for_user(db: AsyncSession, *, user_id: int, tenant_id: int, unread_only: bool) -> list[Notification]:
    stmt = select(Notification).where(Notification.user_id == user_id, Notification.tenant_id == tenant_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return list((await db.execute(
        stmt.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(LIST_LIMIT)
    )).scalars())


async def unread_count(db: AsyncSession, *, user_id: int, tenant_id: int) -> int:
    return (await db.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_id, Notification.tenant_id == tenant_id, Notification.read_at.is_(None),
        )
    )).scalar() or 0


async def mark_read(db: AsyncSession, *, user_id: int, tenant_id: int, ids: list[int] | None) -> int:
    """Marks the user's own notifications read — the given ids, or all of them."""
    stmt = update(Notification).where(
        Notification.user_id == user_id, Notification.tenant_id == tenant_id, Notification.read_at.is_(None),
    )
    if ids is not None:
        stmt = stmt.where(Notification.id.in_(ids))
    result = await db.execute(stmt.values(read_at=datetime.now(timezone.utc)))
    return result.rowcount or 0


def notification_out(n: Notification) -> dict:
    return {
        "id": n.id, "kind": n.kind, "project_id": n.project_id, "flag_id": n.flag_id,
        "record_id": n.record_id, "actor_id": n.actor_id, "message": n.message,
        "created_at": n.created_at, "read_at": n.read_at,
    }

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditEntityType
from app.models.record import Record
from app.services import audit_service


async def acquire_lock(
    db: AsyncSession,
    *,
    record: Record,
    user_id: int,
    tenant_id: int,
) -> None:
    """Acquire a pessimistic lock. Raises 409 if another user holds it."""
    if record.locked_by is not None and record.locked_by != user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Record is locked by another user",
                "locked_by": record.locked_by,
                "locked_at": record.locked_at.isoformat() if record.locked_at else None,
            },
        )
    if record.locked_by != user_id:
        record.locked_by = user_id
        record.locked_at = datetime.now(timezone.utc)
        await audit_service.write_event(
            db,
            tenant_id=tenant_id,
            entity_type=AuditEntityType.record,
            entity_id=record.id,
            action=AuditAction.locked,
            performed_by=user_id,
        )


async def release_lock(
    db: AsyncSession,
    *,
    record: Record,
    user_id: int,
    tenant_id: int,
) -> None:
    """Release a lock. No-op if already unlocked."""
    if record.locked_by is None:
        return
    record.locked_by = None
    record.locked_at = None
    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.record,
        entity_id=record.id,
        action=AuditAction.unlocked,
        performed_by=user_id,
    )

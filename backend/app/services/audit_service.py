from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditEntityType, AuditLog


async def write_event(
    db: AsyncSession,
    *,
    tenant_id: int,
    entity_type: AuditEntityType,
    entity_id: int,
    action: AuditAction,
    performed_by: int | None = None,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Append a single immutable audit event. Never call this from routers — only from services."""
    entry = AuditLog(
        tenant_id=tenant_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        performed_by=performed_by,
        performed_at=datetime.utcnow(),
        old_value=old_value,
        new_value=new_value,
        metadata_=metadata,
    )
    db.add(entry)
    # deliberately NOT committing here — caller owns the transaction
    return entry


async def get_record_history(
    db: AsyncSession,
    *,
    tenant_id: int,
    record_id: int,
) -> list[AuditLog]:
    """Return all audit events for a record, ordered chronologically."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.entity_type == AuditEntityType.record,
            AuditLog.entity_id == record_id,
        )
        .options(selectinload(AuditLog.actor))
        .order_by(AuditLog.performed_at)
    )
    return list(result.scalars().all())

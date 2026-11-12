from datetime import datetime, timezone
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
        performed_at=datetime.now(timezone.utc),
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


async def get_lot_history(
    db: AsyncSession,
    *,
    tenant_id: int,
    lot_id: int,
    from_first_release: bool = False,
) -> list[AuditLog]:
    """Return all audit events for a lot, ordered chronologically.

    from_first_release=True drops everything before the lot was first
    released — the customer's view, which shouldn't include the digitizing
    side's draft-stage work (the outgoing inspection itself is shared
    separately as the inspection report)."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.entity_type == AuditEntityType.lot,
            AuditLog.entity_id == lot_id,
        )
        .options(selectinload(AuditLog.actor))
        .order_by(AuditLog.performed_at, AuditLog.id)
    )
    events = list(result.scalars().all())
    if not from_first_release:
        return events
    first_release = next(
        (
            i for i, e in enumerate(events)
            if e.action == AuditAction.status_changed
            and (e.new_value or {}).get("status") == "released"
        ),
        None,
    )
    return [] if first_release is None else events[first_release:]

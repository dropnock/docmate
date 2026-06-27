from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditEntityType
from app.models.record import Record
from app.models.record_version import RecordVersion, VersionReason
from app.services import audit_service


async def create_version(
    db: AsyncSession,
    *,
    record: Record,
    reason: VersionReason,
    user_id: int,
    tenant_id: int,
) -> RecordVersion:
    """Snapshot the current indexed_data as an immutable version row."""
    version = RecordVersion(
        record_id=record.id,
        version_number=record.current_version,
        indexed_data=record.indexed_data or {},
        created_by=user_id,
        reason=reason,
    )
    db.add(version)
    record.current_version += 1

    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.record,
        entity_id=record.id,
        action=AuditAction.version_created,
        performed_by=user_id,
        new_value={"version_number": version.version_number, "reason": reason.value},
    )
    return version

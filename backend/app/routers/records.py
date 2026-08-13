import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import check_project_access, get_current_user, require_roles
from app.models.record import Record
from app.models.record_version import RecordVersion
from app.schemas.batch import (
    AuditEventOut, ExportRecordsRequest, RecordOut, RecordVersionOut, RequeueRecordsRequest,
)
from app.services import audit_service, record_service, s3_service
from app.services.lock_service import release_lock


class SaveDraftRequest(BaseModel):
    indexed_data: dict

router = APIRouter(prefix="/api/records", tags=["records"])


async def _get_authorized_record(record_id: int, db: AsyncSession, current_user) -> Record:
    """Fetch a record and enforce the same tenant/portal boundary every
    other project-scoped endpoint enforces via check_project_access —
    record_id alone is not sufficient, since it's a bare sequential PK."""
    record = await db.get(Record, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    project = await s3_service.resolve_record_project(record, db)
    check_project_access(project, current_user)
    return record


@router.get("/{record_id}", response_model=RecordOut)
async def get_record(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    record = await _get_authorized_record(record_id, db, current_user)
    await record_service.attach_task_attribution(db, [record])
    return record


@router.post("/requeue", response_model=list[RecordOut])
async def requeue_records(
    body: RequeueRecordsRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    """Supervisor sends one or more records back for re-indexing or a fresh
    internal QA pass, outside the normal per-task workflow. See
    record_service.requeue_record for the state-transition rules."""
    for rid in body.record_ids:
        await _get_authorized_record(rid, db, current_user)
    records = await record_service.bulk_requeue_records(
        db, record_ids=body.record_ids, target=body.target,
        supervisor_id=current_user.id, tenant_id=current_user._tenant_id, note=body.note,
    )
    await record_service.attach_task_attribution(db, records)
    return records


@router.post("/export")
async def export_records(
    body: ExportRecordsRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    """Bulk-downloads selected records' current indexed_data as a ZIP of
    one JSON file per record."""
    for rid in body.record_ids:
        await _get_authorized_record(rid, db, current_user)
    zip_bytes = await record_service.export_records_zip(db, record_ids=body.record_ids)
    filename = f"records_export_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.zip"
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.patch("/{record_id}/draft", response_model=RecordOut)
async def save_draft(
    record_id: int,
    body: SaveDraftRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    record = await _get_authorized_record(record_id, db, current_user)
    if record.locked_by != current_user.id:
        raise HTTPException(status_code=403, detail="You do not hold the lock on this record")
    record.indexed_data = body.indexed_data
    return record


@router.post("/{record_id}/unlock", response_model=RecordOut)
async def unlock_record(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    """Force-releases a record's lock regardless of task state. The normal
    release paths (task_service's complete/fail/reassign, and the 15-minute
    stale_checker job) all key off a specific task's assignee — a lock left
    behind by anything outside those paths (a crash, a race, manual data
    fixes) has no task to reassign it away from, so this is a direct escape
    hatch supervisors can reach for regardless of task state."""
    record = await _get_authorized_record(record_id, db, current_user)
    await release_lock(db, record=record, user_id=current_user.id, tenant_id=current_user._tenant_id)
    return record


@router.get("/{record_id}/versions", response_model=list[RecordVersionOut])
async def get_versions(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await _get_authorized_record(record_id, db, current_user)
    result = await db.execute(
        select(RecordVersion)
        .where(RecordVersion.record_id == record_id)
        .order_by(RecordVersion.version_number)
    )
    return list(result.scalars().all())


@router.get("/{record_id}/history")
async def get_record_history(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    events = await audit_service.get_record_history(
        db, tenant_id=current_user._tenant_id, record_id=record_id
    )
    return [
        {
            "id": e.id,
            "entity_type": e.entity_type.value,
            "entity_id": e.entity_id,
            "action": e.action.value,
            "performed_by": e.performed_by,
            "actor_name": e.actor.full_name if e.actor else None,
            "performed_at": e.performed_at.isoformat(),
            "old_value": e.old_value,
            "new_value": e.new_value,
            "metadata": e.metadata_,
        }
        for e in events
    ]

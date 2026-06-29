from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.record import Record
from app.models.record_version import RecordVersion
from app.schemas.batch import AuditEventOut, RecordOut, RecordVersionOut
from app.services import audit_service


class SaveDraftRequest(BaseModel):
    indexed_data: dict

router = APIRouter(prefix="/api/records", tags=["records"])


@router.get("/{record_id}", response_model=RecordOut)
async def get_record(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    record = await db.get(Record, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    return record


@router.patch("/{record_id}/draft", response_model=RecordOut)
async def save_draft(
    record_id: int,
    body: SaveDraftRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    record = await db.get(Record, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    if record.locked_by != current_user.id:
        raise HTTPException(status_code=403, detail="You do not hold the lock on this record")
    record.indexed_data = body.indexed_data
    return record


@router.get("/{record_id}/versions", response_model=list[RecordVersionOut])
async def get_versions(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
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
    current_user=Depends(get_current_user),
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

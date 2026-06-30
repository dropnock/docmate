from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.schemas.lot import (
    ApplySampleRequest,
    CreateQcBatchesRequest,
    LotCreate,
    LotOut,
)
from app.services import lot_service

router = APIRouter(prefix="/api/lots", tags=["lots"])


@router.post("", status_code=201)
async def create_lot(
    body: LotCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    lot = await lot_service.create_lot(
        db,
        project_id=body.project_id,
        name=body.name,
        description=body.description,
        record_ids=body.record_ids,
        user_id=current_user.id,
        tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return LotOut.model_validate(lot)


@router.get("/project/{project_id}")
async def list_lots(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    lots = await lot_service.list_lots(
        db, project_id=project_id, tenant_id=current_user._tenant_id
    )
    return [LotOut.model_validate(lot) for lot in lots]


@router.get("/{lot_id}")
async def get_lot(
    lot_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from sqlalchemy import select
    from app.models.lot import Lot, LotRecord
    from app.models.record import Record

    lot = await lot_service._get_lot(db, lot_id, current_user._tenant_id)
    lot_records_result = await db.execute(
        select(LotRecord).where(LotRecord.lot_id == lot_id)
    )
    lot_records = list(lot_records_result.scalars().all())

    records_out = []
    for lr in lot_records:
        record = await db.get(Record, lr.record_id)
        if record:
            records_out.append({
                "record_id": record.id,
                "source_identifier": record.source_identifier,
                "original_filename": record.original_filename,
                "status": record.status,
                "is_sampled": lr.is_sampled,
            })

    return {**LotOut.model_validate(lot).model_dump(), "records": records_out}


@router.post("/{lot_id}/release")
async def release_lot(
    lot_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    lot = await lot_service.release_lot(
        db, lot_id=lot_id, user_id=current_user.id, tenant_id=current_user._tenant_id
    )
    await db.commit()
    return LotOut.model_validate(lot)


@router.post("/{lot_id}/sample")
async def apply_sample(
    lot_id: int,
    body: ApplySampleRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("customer_supervisor", "admin")),
):
    lot = await lot_service.apply_sample(
        db,
        lot_id=lot_id,
        sample_rate=body.sample_rate,
        user_id=current_user.id,
        tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return LotOut.model_validate(lot)


@router.post("/{lot_id}/qc-batches", status_code=201)
async def create_qc_batches(
    lot_id: int,
    body: CreateQcBatchesRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("customer_supervisor", "admin")),
):
    batches = await lot_service.create_qc_batches(
        db,
        lot_id=lot_id,
        project_id=body.project_id,
        document_type_id=body.document_type_id,
        assignments=[a.model_dump() for a in body.assignments],
        supervisor_id=current_user.id,
        tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return [{"id": b.id, "name": b.name} for b in batches]


@router.post("/{lot_id}/send-for-remediation")
async def send_for_remediation(
    lot_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("customer_supervisor", "admin")),
):
    lot = await lot_service.send_for_remediation(
        db, lot_id=lot_id, user_id=current_user.id, tenant_id=current_user._tenant_id
    )
    await db.commit()
    return LotOut.model_validate(lot)
